import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';

// Store decorations globally so we can clear them later
let currentDecorations: vscode.TextEditorDecorationType[] = [];

// Diagnostics collection for energy suggestions
let diagnosticCollection: vscode.DiagnosticCollection;

export function activate(context: vscode.ExtensionContext) {
    console.log('EcoCode Profiler is now active!');

    // Create a diagnostics collection for energy suggestions (yellow squiggles)
    diagnosticCollection = vscode.languages.createDiagnosticCollection('ecocode');
    context.subscriptions.push(diagnosticCollection);

    let disposable = vscode.commands.registerCommand('ecocode-profiler.profileCurrentFile', () => {
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

        // Save the document before running if it's dirty
        if (document.isDirty) {
            document.save();
        }

        const filePath = document.uri.fsPath;

        // Read configuration values
        const config = vscode.workspace.getConfiguration('ecocode');
        const carbonFactor = config.get<number>('carbonIntensityFactor', 310.0);

        vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: "EcoCode Profiler: Measuring Energy...",
            cancellable: false
        }, async (progress) => {
            // Step 1: Run the energy profiler
            progress.report({ message: "Running energy profiler..." });
            const report = await runProfiler(context, filePath, carbonFactor);
            if (!report) { return; }

            // Display inline energy annotations
            displayAnnotations(editor, report);

            // Step 2: Run the suggestion engine
            progress.report({ message: "Analysing code for energy anti-patterns..." });

            // Get the names of the profiled functions to scope suggestions
            const functionNames = Object.keys(report.functions);
            const suggestions = await runSuggestionEngine(context, filePath, functionNames);
            if (suggestions) {
                displayDiagnostics(document, suggestions);
            }
        });
    });

    context.subscriptions.push(disposable);
}

// ---------------------------------------------------------------------------
// Backend Runners
// ---------------------------------------------------------------------------

function runProfiler(context: vscode.ExtensionContext, filePath: string, carbonFactor: number): Promise<any | null> {
    return new Promise((resolve) => {
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
            } catch (parseError) {
                vscode.window.showErrorMessage(`EcoCode Profiler: Failed to parse profiler output.`);
                console.error(stdout);
                resolve(null);
            }
        });
    });
}

function runSuggestionEngine(context: vscode.ExtensionContext, filePath: string, functionNames: string[]): Promise<any | null> {
    return new Promise((resolve) => {
        const suggestionsPath = path.join(context.extensionPath, 'backend', 'suggestions.py');

        // Pass each function name as a separate quoted argument to avoid shell splitting issues
        const funcArgs = functionNames.length > 0
            ? `--functions ${functionNames.map(f => `"${f}"`).join(' ')}`
            : '';
        const command = `python "${suggestionsPath}" "${filePath}" ${funcArgs}`;

        console.log(`[EcoCode] Running suggestion engine: ${command}`);

        cp.exec(command, (error, stdout, stderr) => {
            if (error) {
                vscode.window.showErrorMessage(`EcoCode Suggestion Engine Error: ${stderr || error.message}`);
                console.error('[EcoCode] Suggestion engine error:', stderr);
                resolve(null);
                return;
            }
            if (!stdout || stdout.trim() === '') {
                vscode.window.showWarningMessage('EcoCode: Suggestion engine returned no output.');
                resolve(null);
                return;
            }
            try {
                const result = JSON.parse(stdout);
                console.log(`[EcoCode] Suggestions received:`, JSON.stringify(result));
                resolve(result);
            } catch (parseError) {
                vscode.window.showErrorMessage(`EcoCode: Failed to parse suggestion output. Raw: ${stdout.substring(0, 200)}`);
                resolve(null);
            }
        });
    });
}

// ---------------------------------------------------------------------------
// UI Renderers
// ---------------------------------------------------------------------------

function displayAnnotations(editor: vscode.TextEditor, report: any) {
    // Clear old decorations
    currentDecorations.forEach(d => d.dispose());
    currentDecorations = [];

    const text = editor.document.getText();
    const functions = report.functions;
    const isHardwareMeasured = report.metadata.hardware_measured;

    // FR-09: Explicitly label as Measured vs Estimated
    const badgeText = isHardwareMeasured ? "⚡ Measured (RAPL)" : "💻 Estimated (TDP fallback)";

    vscode.window.showInformationMessage(`EcoCode Profiler: Profiling complete. Mode: ${badgeText}`);

    for (const [funcName, stats] of Object.entries<any>(functions)) {
        // Simple regex to find the function definition line
        const regex = new RegExp(`def\\s+${funcName}\\s*\\(`, 'g');
        let match;

        while ((match = regex.exec(text)) !== null) {
            const startPos = editor.document.positionAt(match.index);
            const line = editor.document.lineAt(startPos.line);

            // FR-08: Display per-function energy as inline annotations
            const decorationType = vscode.window.createTextEditorDecorationType({
                after: {
                    contentText: `  [🌍 ${stats.carbon_gco2e.toFixed(6)} gCO₂e | 🔋 ${stats.total_energy_j.toFixed(4)} J]`,
                    color: new vscode.ThemeColor('editorLineNumber.foreground'),
                    fontStyle: 'italic',
                    margin: '0 0 0 20px',
                    fontWeight: 'bold'
                }
            });

            const range = new vscode.Range(line.range.end, line.range.end);
            editor.setDecorations(decorationType, [{ range }]);
            currentDecorations.push(decorationType);
        }
    }
}

function displayDiagnostics(document: vscode.TextDocument, suggestionsReport: any) {
    // Clear old diagnostics for this file
    diagnosticCollection.delete(document.uri);

    const diagnostics: vscode.Diagnostic[] = [];

    for (const suggestion of suggestionsReport.suggestions) {
        // VS Code lines are 0-indexed, our suggestions are 1-indexed
        const lineIndex = suggestion.line - 1;
        const line = document.lineAt(lineIndex);
        const range = new vscode.Range(line.range.start, line.range.end);

        const severity = suggestion.severity === 'error'
            ? vscode.DiagnosticSeverity.Error
            : vscode.DiagnosticSeverity.Warning;

        const diagnostic = new vscode.Diagnostic(
            range,
            `⚡ ${suggestion.message} [${suggestion.estimated_savings} savings — ${suggestion.source}]`,
            severity
        );
        diagnostic.source = 'EcoCode Profiler';
        diagnostic.code = suggestion.pattern;

        diagnostics.push(diagnostic);
    }

    diagnosticCollection.set(document.uri, diagnostics);

    if (diagnostics.length > 0) {
        vscode.window.showInformationMessage(
            `🌿 EcoCode found ${diagnostics.length} energy optimisation suggestion(s). Check the Problems panel.`
        );
    }
}

export function deactivate() {
    currentDecorations.forEach(d => d.dispose());
    if (diagnosticCollection) {
        diagnosticCollection.dispose();
    }
}
