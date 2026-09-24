import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';

// Store decorations globally so we can clear them later
let currentDecorations: vscode.TextEditorDecorationType[] = [];

export function activate(context: vscode.ExtensionContext) {
    console.log('EcoCode Profiler is now active!');

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
            return new Promise<void>((resolve, reject) => {
                // Determine path to the python profiler backend
                const profilerPath = path.join(context.extensionPath, 'backend', 'profiler.py');
                
                // Spawn the python process. Assumes 'python' is in PATH.
                const command = `python "${profilerPath}" "${filePath}" --carbon ${carbonFactor}`;
                
                cp.exec(command, (error, stdout, stderr) => {
                    if (error) {
                        vscode.window.showErrorMessage(`EcoCode Profiler Error: ${stderr || error.message}`);
                        resolve();
                        return;
                    }
                    
                    try {
                        const report = JSON.parse(stdout);
                        displayAnnotations(editor, report);
                        resolve();
                    } catch (parseError) {
                        vscode.window.showErrorMessage(`EcoCode Profiler: Failed to parse backend output.`);
                        console.error(stdout);
                        resolve();
                    }
                });
            });
        });
    });

    context.subscriptions.push(disposable);
}

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

export function deactivate() {
    currentDecorations.forEach(d => d.dispose());
}
