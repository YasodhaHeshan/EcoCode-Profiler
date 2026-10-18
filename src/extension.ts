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

    const exportButton = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    exportButton.text = '$(export) Export EcoCode Report';
    exportButton.tooltip = 'Export a formatted EcoCode energy report';
    exportButton.command = 'ecocode-profiler.exportReport';
    exportButton.show();
    context.subscriptions.push(exportButton);

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
            saveLabel: 'Export Formatted EcoCode Report',
            defaultUri: vscode.Uri.file(path.join(path.dirname(report.filePath), `${path.parse(report.filePath).name}.ecocode-report.html`)),
            filters: { 'HTML report': ['html'] },
        });
        if (!destination) return;
        await vscode.workspace.fs.writeFile(destination, Buffer.from(renderHtmlReport(report), 'utf8'));
        vscode.window.showInformationMessage(`Formatted EcoCode report exported to ${destination.fsPath}`);
    }));
}

function renderHtmlReport(stored: StoredReport): string {
    const metadata = stored.report.metadata || {};
    const functions = Object.entries(stored.report.functions || {})
        .filter(([name]) => !name.startsWith('<'))
        .sort((a, b) => Number(b[1].total_energy_j || 0) - Number(a[1].total_energy_j || 0));
    const totalEnergy = Number(metadata.total_estimated_energy_j || 0);
    const totalCarbon = functions.reduce((sum, [, stats]) => sum + Number(stats.carbon_gco2e || 0), 0);
    const escape = (value: unknown) => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const format = (value: unknown, digits = 4) => Number(value || 0).toFixed(digits);
    const chartFunctions = functions.slice(0, 8);
    const maxEnergy = Math.max(0, ...chartFunctions.map(([, stats]) => Number(stats.total_energy_j || 0)));
    const maxTime = Math.max(0, ...chartFunctions.map(([, stats]) => Number(stats.total_time_s || 0)));
    const chartRows = (key: 'total_energy_j' | 'total_time_s', maximum: number, suffix: string, digits: number) => chartFunctions.map(([name, stats]) => {
        const value = Number(stats[key] || 0);
        const width = maximum > 0 ? Math.max(3, (value / maximum) * 100) : 0;
        return `<div class="chart-row"><span title="${escape(name)}">${escape(name)}</span><div class="bar-track"><div class="bar" style="width:${width}%"></div></div><strong>${format(value, digits)} ${suffix}</strong></div>`;
    }).join('') || '<p class="empty">No function data is available for charts.</p>';
    const functionRows = functions.map(([name, stats]) => `
        <tr><td><strong>${escape(name)}</strong></td><td>${format(stats.calls, 0)}</td><td>${format(stats.total_time_s, 6)} s</td><td>${format(stats.total_energy_j)} J</td><td>${format(stats.carbon_gco2e, 8)} gCO₂e</td></tr>`).join('') || '<tr><td colspan="5" class="empty">No profiled functions were recorded.</td></tr>';
    const suggestionRows = stored.suggestions.map(suggestion => `
        <article class="suggestion"><div><span class="badge">${escape(suggestion.estimated_savings)}</span><h3>${escape(suggestion.pattern)}</h3><p>${escape(suggestion.message)}</p><small>Line ${escape(suggestion.line)} · ${escape(suggestion.source)}</small></div><pre><code>${escape(suggestion.suggested_code || 'Review this refactoring in context before applying it.')}</code></pre></article>`).join('') || '<p class="empty">No energy-efficiency suggestions were found for the profiled functions.</p>';
    const method = metadata.energy_estimation?.method === 'rapl_measurement' ? 'Measured with Intel RAPL' : metadata.energy_estimation?.method === 'intel_energy_model' ? 'Intel energy model estimate' : 'TDP software estimate';
    return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EcoCode Energy Report</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f7f5;color:#18322b;font:15px/1.55 Inter,Segoe UI,Arial,sans-serif}.wrap{max-width:1050px;margin:auto;padding:42px 24px 64px}.hero{background:linear-gradient(125deg,#073b32,#0c6b55);color:#fff;border-radius:20px;padding:36px 40px;box-shadow:0 18px 40px #173d3328}.hero h1{font-size:32px;margin:0 0 6px}.hero p{margin:0;color:#d1eee4}.meta{margin-top:26px;font-size:13px;color:#dcefe9}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}.card,.panel{background:#fff;border:1px solid #dce9e3;border-radius:16px;box-shadow:0 4px 14px #173d330b}.card{padding:20px}.card span{display:block;color:#5a766c;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}.card strong{display:block;font-size:25px;margin-top:5px;color:#0a4e40}.panel{padding:25px;margin-top:20px}.panel h2{margin:0 0 16px;font-size:20px;color:#0a4e40}.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.chart h3{font-size:14px;margin:0 0 13px;color:#547268}.chart-row{display:grid;grid-template-columns:minmax(70px,120px) 1fr auto;gap:9px;align-items:center;margin:9px 0;font-size:12px}.chart-row>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-track{height:12px;background:#e7f0ec;border-radius:99px;overflow:hidden}.bar{height:100%;background:linear-gradient(90deg,#12a879,#0c6b55);border-radius:99px}.chart-row strong{font-size:11px;color:#406257}table{width:100%;border-collapse:collapse}th{text-align:left;color:#547268;font-size:12px;text-transform:uppercase;letter-spacing:.06em}th,td{padding:12px 10px;border-bottom:1px solid #e5efeb}.suggestion{border:1px solid #d9e8e2;border-left:5px solid #f2b93d;border-radius:10px;padding:16px 18px;margin:12px 0;display:grid;grid-template-columns:1fr minmax(250px,.9fr);gap:18px}.suggestion h3{margin:0 0 4px;text-transform:capitalize}.suggestion p{margin:6px 0}.badge{float:right;background:#fff3cf;color:#765309;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700}pre{margin:0;background:#102921;color:#e7f6ef;border-radius:9px;padding:13px;white-space:pre-wrap;overflow-wrap:anywhere}.empty{color:#6d8179;text-align:center;padding:18px}@media(max-width:700px){.grid,.chart-grid{grid-template-columns:1fr}.suggestion{grid-template-columns:1fr}.hero{padding:28px 24px}}
</style></head><body><main class="wrap"><section class="hero"><h1>🌍 EcoCode Energy Report</h1><p>Energy and carbon profile for <strong>${escape(path.basename(stored.filePath))}</strong></p><div class="meta">Generated ${escape(new Date(stored.updatedAt).toLocaleString())} · ${escape(method)}</div></section>
<section class="grid"><div class="card"><span>Total energy</span><strong>${format(totalEnergy)} J</strong></div><div class="card"><span>Estimated carbon</span><strong>${format(totalCarbon, 8)} gCO₂e</strong></div><div class="card"><span>Execution time</span><strong>${format(metadata.total_run_time_s, 6)} s</strong></div></section>
<section class="panel"><h2>Energy charts</h2><div class="chart-grid"><div class="chart"><h3>Energy by function (top 8)</h3>${chartRows('total_energy_j', maxEnergy, 'J', 4)}</div><div class="chart"><h3>Runtime by function (top 8)</h3>${chartRows('total_time_s', maxTime, 's', 6)}</div></div></section>
<section class="panel"><h2>Function profile</h2><table><thead><tr><th>Function</th><th>Calls</th><th>Time</th><th>Energy</th><th>Carbon</th></tr></thead><tbody>${functionRows}</tbody></table></section>
<section class="panel"><h2>Energy-efficiency suggestions</h2>${suggestionRows}</section>
</main></body></html>`;
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
