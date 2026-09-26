import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';

const STORAGE_KEY = 'ecocode.profileReports.v1';
const SUMMARY_VIEW_ID = 'ecocode.summary';
const HEAT_COLORS = [
    'rgba(255, 235, 59, 0.20)',
    'rgba(255, 193, 7, 0.34)',
    'rgba(255, 152, 0, 0.48)',
    'rgba(244, 67, 54, 0.64)',
];

interface ProfileReport {
    metadata: Record<string, any>;
    functions: Record<string, any>;
}

interface Suggestion {
    line: number;
    function: string;
    pattern: string;
    message: string;
    severity: string;
    estimated_savings: string;
    source: string;
    confidence?: number;
    suggested_code?: string;
}

interface SuggestionEngineResult {
    model_type?: string;
    model?: string;
    warning?: string;
    suggestions: Suggestion[];
}

interface StoredReport {
    uri: string;
    filePath: string;
    updatedAt: number;
    report: ProfileReport;
    suggestions: Suggestion[];
}

type SummaryNode = { kind: 'function'; name: string; stats: any } | { kind: 'suggestion'; suggestion: Suggestion };

class SummaryProvider implements vscode.TreeDataProvider<SummaryNode> {
    private readonly changed = new vscode.EventEmitter<SummaryNode | undefined | null | void>();
    readonly onDidChangeTreeData = this.changed.event;
    private current: StoredReport | undefined;

    setReport(report: StoredReport | undefined): void {
        this.current = report;
        this.changed.fire();
    }

    getTreeItem(node: SummaryNode): vscode.TreeItem {
        if (node.kind === 'function') {
            const topFunction = Object.entries(this.current?.report.functions || {})
                .filter(([name, stats]) => !name.startsWith('<') && Object.keys(stats.line_time_s || {}).length > 0)
                .sort((a, b) => Number(b[1].total_energy_j || 0) - Number(a[1].total_energy_j || 0))[0];
            const hasLineTimings = Object.keys(node.stats.line_time_s || {}).length > 0;
            const label = topFunction?.[0] === node.name && hasLineTimings ? `${node.name} (heatmap)` : node.name;
            const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Collapsed);
            item.description = `${Number(node.stats.total_energy_j || 0).toFixed(4)} J`;
            item.tooltip = `${node.name}: ${Number(node.stats.total_time_s || 0).toFixed(6)} s, ${Number(node.stats.carbon_gco2e || 0).toFixed(8)} gCO₂e`;
            item.contextValue = 'ecocodeFunction';
            return item;
        }
        const item = new vscode.TreeItem(`${node.suggestion.pattern}: ${node.suggestion.message}`, vscode.TreeItemCollapsibleState.None);
        item.description = `${node.suggestion.estimated_savings} · line ${node.suggestion.line}`;
        item.tooltip = node.suggestion.suggested_code || node.suggestion.source;
        return item;
    }

    getChildren(node?: SummaryNode): SummaryNode[] {
        if (!this.current) return [];
        if (node?.kind === 'function') {
            return this.current.suggestions
                .filter(s => s.function === node.name)
                .map(suggestion => ({ kind: 'suggestion', suggestion }));
        }
        if (node) return [];
        return Object.entries(this.current.report.functions || {})
            .map(([name, stats]) => ({ kind: 'function' as const, name, stats }))
            .sort((a, b) => Number(b.stats.total_energy_j || 0) - Number(a.stats.total_energy_j || 0));
    }
}

let diagnosticCollection: vscode.DiagnosticCollection;
let summaryProvider: SummaryProvider;
let storedReports: Record<string, StoredReport> = {};
const decorationsByUri = new Map<string, vscode.TextEditorDecorationType[]>();

export async function activate(context: vscode.ExtensionContext) {
    console.log('EcoCode Profiler is now active!');
    diagnosticCollection = vscode.languages.createDiagnosticCollection('ecocode');
    context.subscriptions.push(diagnosticCollection);

    summaryProvider = new SummaryProvider();
    context.subscriptions.push(vscode.window.registerTreeDataProvider(SUMMARY_VIEW_ID, summaryProvider));

    storedReports = context.workspaceState.get<Record<string, StoredReport>>(STORAGE_KEY, {});
    const latest = Object.values(storedReports).sort((a, b) => b.updatedAt - a.updatedAt)[0];
    summaryProvider.setReport(latest);
    for (const editor of vscode.window.visibleTextEditors) restoreForEditor(editor);
    context.subscriptions.push(vscode.window.onDidChangeVisibleTextEditors(editors => {
        for (const editor of editors) restoreForEditor(editor);
    }));
    context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(document => {
        const saved = storedReports[document.uri.toString()];
        if (saved) restoreDiagnostics(document, saved.suggestions);
    }));

    context.subscriptions.push(vscode.commands.registerCommand('ecocode-profiler.profileCurrentFile', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showErrorMessage('EcoCode Profiler: No active editor found.');
            return;
        }
        const document = editor.document;
        if (document.languageId !== 'python') {
            vscode.window.showErrorMessage('EcoCode Profiler: Only Python files are supported.');
            return;
        }
        if (document.isDirty && !(await document.save())) return;

        const config = vscode.workspace.getConfiguration('ecocode');
        const carbonFactor = config.get<number>('carbonIntensityFactor', 310.0);
        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: 'EcoCode Profiler: Measuring Energy...',
            cancellable: false,
        }, async progress => {
            progress.report({ message: 'Running energy profiler...' });
            const report = await runProfiler(context, document.uri.fsPath, carbonFactor);
            if (!report) return;

            progress.report({ message: 'Analysing code for energy anti-patterns...' });
            const suggestionEndpoint = config.get<string>('suggestionModel.endpoint', '');
            const suggestionModel = config.get<string>('suggestionModel.name', 'qwen2.5-coder:7b');
            const suggestionTimeout = config.get<number>('suggestionModel.timeoutSeconds', 180);
            const suggestions = await runSuggestionEngine(context, document.uri.fsPath, Object.keys(report.functions || {}), suggestionEndpoint, suggestionModel, suggestionTimeout);
            if (suggestions?.warning) {
                vscode.window.showWarningMessage(`EcoCode AI suggestions: ${suggestions.warning}`);
            } else if (suggestions?.model_type === 'general_code_model') {
                vscode.window.showInformationMessage(`EcoCode AI suggestions generated by ${suggestions.model}.`);
            }
            const stored: StoredReport = {
                uri: document.uri.toString(),
                filePath: document.uri.fsPath,
                updatedAt: Date.now(),
                report,
                suggestions: suggestions?.suggestions || [],
            };
            storedReports[stored.uri] = stored;
            await context.workspaceState.update(STORAGE_KEY, storedReports);
            summaryProvider.setReport(stored);
            restoreForEditor(editor);
            vscode.window.showInformationMessage(`EcoCode Profiler: Report saved. Energy estimate: ${Number(report.metadata.total_estimated_energy_j || 0).toFixed(4)} J.`);
        });
    }));

    context.subscriptions.push(vscode.commands.registerCommand('ecocode-profiler.exportReport', async () => {
        const currentUri = vscode.window.activeTextEditor?.document.uri.toString();
        const latest = currentUri ? storedReports[currentUri] : undefined;
        const report = latest || Object.values(storedReports).sort((a, b) => b.updatedAt - a.updatedAt)[0];
        if (!report) {
            vscode.window.showWarningMessage('EcoCode Profiler: Profile a Python file before exporting a report.');
            return;
        }
        const destination = await vscode.window.showSaveDialog({
            saveLabel: 'Export EcoCode Report',
            defaultUri: vscode.Uri.file(`${report.filePath}.ecocode-report.json`),
            filters: { JSON: ['json'] },
        });
        if (!destination) return;
        const payload = {
            file: report.filePath,
            generated_at: new Date(report.updatedAt).toISOString(),
            profile: report.report,
            suggestions: report.suggestions,
        };
        await vscode.workspace.fs.writeFile(destination, Buffer.from(JSON.stringify(payload, null, 2) + '\n', 'utf8'));
        vscode.window.showInformationMessage(`EcoCode report exported to ${destination.fsPath}`);
    }));
}

function runProfiler(context: vscode.ExtensionContext, filePath: string, carbonFactor: number): Promise<ProfileReport | null> {
    return new Promise(resolve => {
        const profilerPath = path.join(context.extensionPath, 'backend', 'profiler.py');
        const command = `python "${profilerPath}" "${filePath}" --carbon ${carbonFactor}`;
        cp.exec(command, (error, stdout, stderr) => {
            if (error) {
                vscode.window.showErrorMessage(`EcoCode Profiler Error: ${stderr || error.message}`);
                resolve(null);
                return;
            }
            try {
                resolve(JSON.parse(stdout));
            } catch {
                vscode.window.showErrorMessage('EcoCode Profiler: Failed to parse profiler output.');
                console.error(stdout);
                resolve(null);
            }
        });
    });
}

function runSuggestionEngine(context: vscode.ExtensionContext, filePath: string, functionNames: string[], aiEndpoint = '', aiModel = '', aiTimeout = 180): Promise<SuggestionEngineResult | null> {
    return new Promise(resolve => {
        const suggestionsPath = path.join(context.extensionPath, 'backend', 'suggestions.py');
        const args = [suggestionsPath, filePath];
        if (functionNames.length) args.push('--functions', ...functionNames);
        if (aiEndpoint) args.push('--ai-endpoint', aiEndpoint, '--ai-model', aiModel, '--ai-timeout', String(aiTimeout));
        cp.execFile('python', args, (error, stdout, stderr) => {
            if (error) {
                vscode.window.showErrorMessage(`EcoCode Suggestion Engine Error: ${stderr || error.message}`);
                resolve(null);
                return;
            }
            try {
                resolve(JSON.parse(stdout));
            } catch {
                vscode.window.showErrorMessage('EcoCode: Failed to parse suggestion output.');
                resolve(null);
            }
        });
    });
}

function restoreForEditor(editor: vscode.TextEditor): void {
    const stored = storedReports[editor.document.uri.toString()];
    if (!stored) return;
    renderDecorations(editor, stored);
    restoreDiagnostics(editor.document, stored.suggestions);
}

function restoreDiagnostics(document: vscode.TextDocument, suggestions: Suggestion[]): void {
    const diagnostics: vscode.Diagnostic[] = [];
    for (const suggestion of suggestions) {
        const lineIndex = suggestion.line - 1;
        if (lineIndex < 0 || lineIndex >= document.lineCount) continue;
        const line = document.lineAt(lineIndex);
        const severity = suggestion.severity === 'error' ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning;
        const diagnostic = new vscode.Diagnostic(
            line.range,
            `⚡ ${suggestion.message} [${suggestion.estimated_savings} savings — ${suggestion.source}]` +
                (suggestion.suggested_code ? `\n\nSuggested rewrite (adapt names/context):\n${suggestion.suggested_code}` : ''),
            severity,
        );
        diagnostic.source = 'EcoCode Profiler';
        diagnostic.code = suggestion.pattern;
        diagnostics.push(diagnostic);
    }
    diagnosticCollection.set(document.uri, diagnostics);
}

function renderDecorations(editor: vscode.TextEditor, stored: StoredReport): void {
    const uri = editor.document.uri.toString();
    for (const old of decorationsByUri.get(uri) || []) {
        editor.setDecorations(old, []);
        old.dispose();
    }
    const created: vscode.TextEditorDecorationType[] = [];
    const text = editor.document.getText();
    const functionEntries = Object.entries(stored.report.functions || {});
    const estimation = stored.report.metadata.energy_estimation?.method;
    const mode = estimation === 'intel_energy_model' ? 'Intel model estimate' : 'TDP estimate';
    vscode.window.setStatusBarMessage(`EcoCode: ${mode}`, 5000);

    for (const [functionName, stats] of functionEntries) {
        const regex = new RegExp(`(?:async\\s+)?def\\s+${escapeRegExp(functionName)}\\s*\\(`, 'g');
        let match: RegExpExecArray | null;
        while ((match = regex.exec(text)) !== null) {
            const position = editor.document.positionAt(match.index);
            const line = editor.document.lineAt(position.line);
            const annotation = vscode.window.createTextEditorDecorationType({
                after: {
                    contentText: `  [🌍 ${Number(stats.carbon_gco2e || 0).toFixed(6)} gCO₂e | 🔋 ${Number(stats.total_energy_j || 0).toFixed(4)} J]`,
                    color: new vscode.ThemeColor('editorLineNumber.foreground'),
                    fontStyle: 'italic',
                    margin: '0 0 0 20px',
                    fontWeight: 'bold',
                },
            });
            editor.setDecorations(annotation, [new vscode.Range(line.range.end, line.range.end)]);
            created.push(annotation);
        }
    }

    // Heatmap the source lines attributed to the most energy-consuming function.
    const mostExpensive = functionEntries
        .filter(([name, stats]) => !name.startsWith('<') && Object.keys(stats.line_time_s || {}).length > 0)
        .sort((a, b) => Number(b[1].total_energy_j || 0) - Number(a[1].total_energy_j || 0))[0];
    if (mostExpensive) {
        const [name, stats] = mostExpensive;
        const lineTimes = stats.line_time_s || {};
        const maxTime = Math.max(0, ...Object.values<number>(lineTimes));
        const start = findFunctionStart(editor.document, name);
        if (start >= 0 && maxTime > 0) {
            const end = findFunctionEnd(editor.document, start);
            const byBin: vscode.Range[][] = HEAT_COLORS.map(() => []);
            for (const [lineText, duration] of Object.entries<number>(lineTimes)) {
                const lineNumber = Number(lineText) - 1;
                if (lineNumber < start || lineNumber >= end || lineNumber >= editor.document.lineCount || duration <= 0) continue;
                const bin = Math.min(HEAT_COLORS.length - 1, Math.floor((duration / maxTime) * HEAT_COLORS.length));
                const line = editor.document.lineAt(lineNumber);
                byBin[bin].push(new vscode.Range(line.range.start, line.range.end));
            }
            byBin.forEach((ranges, index) => {
                if (!ranges.length) return;
                const heat = vscode.window.createTextEditorDecorationType({
                    backgroundColor: HEAT_COLORS[index],
                    isWholeLine: true,
                    overviewRulerColor: HEAT_COLORS[index],
                    overviewRulerLane: vscode.OverviewRulerLane.Right,
                });
                editor.setDecorations(heat, ranges);
                created.push(heat);
            });
        }
    }
    decorationsByUri.set(uri, created);
}

function findFunctionStart(document: vscode.TextDocument, name: string): number {
    const expression = new RegExp(`^(\\s*)(?:async\\s+)?def\\s+${escapeRegExp(name)}\\s*\\(`);
    for (let line = 0; line < document.lineCount; line++) {
        if (expression.test(document.lineAt(line).text)) return line;
    }
    return -1;
}

function findFunctionEnd(document: vscode.TextDocument, start: number): number {
    const declaration = document.lineAt(start).text;
    const indent = declaration.length - declaration.trimStart().length;
    for (let line = start + 1; line < document.lineCount; line++) {
        const text = document.lineAt(line).text;
        if (!text.trim()) continue;
        const currentIndent = text.length - text.trimStart().length;
        if (currentIndent <= indent && /^(?:async\s+)?(?:def|class)\s+/.test(text.trimStart())) return line;
    }
    return document.lineCount;
}

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function deactivate(): void {
    for (const decorations of decorationsByUri.values()) decorations.forEach(decoration => decoration.dispose());
    diagnosticCollection?.dispose();
}
