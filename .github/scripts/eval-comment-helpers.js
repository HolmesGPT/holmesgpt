/**
 * Eval comment helpers for GitHub workflow PR comments.
 *
 * SECURITY NOTE: This file is loaded from a TRUSTED checkout of the base branch
 * (master), NOT from the PR branch. The workflow checks out to sibling directories:
 *   - PR code: ./code/
 *   - Trusted helpers: ./.trusted/
 *
 * Since they're siblings, neither can overwrite the other.
 * The workflow loads: require('./.trusted/.github/scripts/eval-comment-helpers.js')
 *
 * DO NOT change the workflow to load from ./code/ path!
 */

// Identifier for the persistent automated eval comment (hidden HTML comment)
const AUTO_EVAL_COMMENT_IDENTIFIER = '<!-- holmes-auto-eval-results -->';

// Marker to delimit end of history run content (avoids issues with nested <details> tags)
const HISTORY_RUN_END_MARKER = '<!-- END_HISTORY_RUN -->';

/**
 * Parse run history from an existing comment body
 * @param {string} body - Existing comment body
 * @returns {Array<{summary: string, content: string}>} Array of previous runs
 */
function parseRunHistory(body) {
  const runs = [];

  // Match collapsed previous runs using our unique end marker to handle nested <details> tags
  // The marker ensures we capture the full content even if it contains its own <details> sections
  const historyRegex = /<details>\s*<summary>📜\s*(.+?)<\/summary>\s*([\s\S]*?)<!-- END_HISTORY_RUN -->\s*<\/details>/g;
  let match;
  while ((match = historyRegex.exec(body)) !== null) {
    runs.push({
      summary: match[1].trim(),
      content: match[2].trim()
    });
  }

  return runs;
}

/**
 * Extract the current (uncollapsed) run content from comment body
 * With the new structure, history is at the top, so current run comes after the "---" separator
 * Only extracts completed runs (not in-progress) to avoid saving incomplete results
 * @param {string} body - Existing comment body
 * @returns {{summary: string, content: string}|null} Current run info or null if not a completed run
 */
function extractCurrentRun(body) {
  // Remove the identifier comment
  let cleanBody = body.replace(AUTO_EVAL_COMMENT_IDENTIFIER, '').trim();

  // Skip past the "Previous Runs" section if present (new structure has history at top)
  const previousRunsHeader = '## 📂 Previous Runs';
  if (cleanBody.startsWith(previousRunsHeader)) {
    // Find the separator that ends the history section
    const separatorIndex = cleanBody.indexOf('\n---\n');
    if (separatorIndex !== -1) {
      cleanBody = cleanBody.substring(separatorIndex + 5).trim(); // Skip past "---\n"
    }
  }

  // Find the header line (## ✅ Results... or ## ⏳ HolmesGPT evals running...)
  const headerMatch = cleanBody.match(/^(## [^\n]+)/);
  if (!headerMatch) return null;

  const header = headerMatch[1];

  // Only save completed runs to history (not in-progress runs)
  // Completed runs have "Results" in the title
  if (!header.includes('Results')) {
    return null;
  }

  // Find where footer starts - look for any footer section (Legend, Re-run, or Valid markers)
  const footerMarkers = [
    '<details>\n<summary>📖 <b>Legend</b>',
    '<details>\n<summary>🔄 <b>Re-run evals manually</b>',
    '<details>\n<summary>🏷️ <b>Valid markers</b>',
    '\n---\n**Commands:**'
  ];

  // Find the earliest footer marker
  let endPos = cleanBody.length;
  for (const marker of footerMarkers) {
    const pos = cleanBody.indexOf(marker);
    if (pos !== -1 && pos < endPos) {
      endPos = pos;
    }
  }

  // Extract trigger info for the summary (search in the current run section)
  const currentSection = cleanBody.substring(0, endPos);
  const triggerMatch = currentSection.match(/Automatically triggered by ([^\n]+)/);
  const trigger = triggerMatch ? triggerMatch[1] : '';

  // Extract workflow run URL for linking
  const runUrlMatch = currentSection.match(/\[View workflow logs\]\(([^)]+)\)/);
  const runUrl = runUrlMatch ? runUrlMatch[1] : '';

  // Build a descriptive summary with trigger info
  let summary = 'Previous Run';
  if (trigger) {
    // Extract commit and branch info from trigger like "commit abc1234 on branch `feature`"
    const commitMatch = trigger.match(/commit ([a-f0-9]+)/);
    const commit = commitMatch ? commitMatch[1] : '';
    summary = commit ? `Run @ ${commit}` : `Run: ${trigger.substring(0, 50)}`;
  }
  if (runUrl) {
    // Extract run ID from URL for reference
    const runIdMatch = runUrl.match(/runs\/(\d+)/);
    if (runIdMatch) {
      summary += ` (#${runIdMatch[1]})`;
    }
  }

  // Build content for when this run becomes collapsed
  const content = cleanBody.substring(0, endPos).trim();

  return {
    summary: summary,
    content: content
  };
}

// GitHub comment body size limit (64KB), with buffer for safety
const MAX_COMMENT_SIZE = 60000;

/**
 * Build comment body with run history support for automated runs
 * Previous runs appear at the top in a collapsible section, followed by current run
 * Automatically truncates history if approaching GitHub's 64KB comment limit
 * @param {string} currentContent - Current run's full content (before footer)
 * @param {Array<{summary: string, content: string}>} previousRuns - Previous runs to collapse
 * @param {string} footer - Footer content (legend, rerun instructions)
 * @param {number} maxHistory - Maximum number of historical runs to keep (default 5)
 * @returns {string} Complete comment body with identifier
 */
function buildAutoCommentWithHistory(currentContent, previousRuns, footer, maxHistory = 5) {
  let body = AUTO_EVAL_COMMENT_IDENTIFIER + '\n';

  // Build previous runs section first (at top)
  const runsToShow = previousRuns.slice(0, maxHistory);
  let historySection = '';
  let addedCount = 0;

  if (runsToShow.length > 0) {
    historySection = '## 📂 Previous Runs\n\n';

    for (const run of runsToShow) {
      // Use END_HISTORY_RUN marker to properly delimit content (handles nested <details> tags in reports)
      const historyEntry = `<details>\n<summary>📜 ${run.summary}</summary>\n\n${run.content}\n\n${HISTORY_RUN_END_MARKER}\n</details>\n\n`;

      // Check if adding this entry would exceed the limit
      const projectedSize = body.length + historySection.length + historyEntry.length + currentContent.length + footer.length;
      if (projectedSize > MAX_COMMENT_SIZE) {
        // Add truncation notice instead
        const remaining = runsToShow.length - addedCount;
        historySection += `<details>\n<summary>⚠️ ${remaining} older run${remaining > 1 ? 's' : ''} truncated</summary>\n\n_Older runs were omitted to stay under GitHub's 64KB comment size limit._\n</details>\n\n`;
        break;
      }

      historySection += historyEntry;
      addedCount++;
    }

    historySection += '---\n\n';
  }

  // Assemble: identifier + history + current + footer
  body += historySection;
  body += currentContent;
  body += footer;

  return body;
}

/**
 * Build params object from raw step outputs
 * Handles all type conversions and defaults in one place
 * @param {Object} raw - Raw outputs from GitHub Actions steps
 * @returns {Object} Normalized params object
 */
function buildParams(raw) {
  return {
    isManual: raw.is_manual === 'true',
    trigger: raw.trigger_source,
    model: raw.model || 'default',
    markers: raw.markers,
    filter: raw.filter,
    iterations: raw.iterations,
    branch: raw.branch || '',
    displayBranch: raw.display_branch || '',
    runUrl: raw.run_url,
    prNumber: raw.pr_number ? parseInt(raw.pr_number, 10) : null,
    commentId: raw.comment_id ? parseInt(raw.comment_id, 10) : null,
    testCount: raw.test_count || '0',
    testPreview: raw.test_preview || '',
    duration: raw.duration || 'N/A',
    validMarkers: raw.valid_markers || '',
    triggered_by: raw.triggered_by || ''
  };
}

/**
 * Render a progress checklist
 * @param {Array<[boolean, string]>} steps - Array of [completed, text] tuples
 * @returns {string} Markdown checklist
 */
function renderProgress(steps) {
  return steps.map(([done, text]) =>
    done ? `- [x] ${text}` : `- [ ] ${text}`
  ).join('\n');
}

/**
 * Render parameters table for manual runs
 * @param {Object} p - Parameters object
 * @param {Object} context - GitHub context object (optional, for rerun link)
 * @returns {string} Markdown table
 */
function renderParamsTable(p, context = null) {
  let workflowLinks = `[View logs](${p.runUrl})`;
  if (context) {
    const baseWorkflowUrl = `https://github.com/${context.repo.owner}/${context.repo.repo}/actions/workflows/eval-regression.yaml`;
    const rerunUrl = p.displayBranch ? `${baseWorkflowUrl}?ref=${encodeURIComponent(p.displayBranch)}` : baseWorkflowUrl;
    workflowLinks += ` \\| [Rerun](${rerunUrl})`;
  }
  return `| Parameter | Value |\n|-----------|-------|\n` +
    `| **Triggered via** | ${p.trigger} |\n` +
    (p.displayBranch ? `| **Branch** | \`${p.displayBranch}\` |\n` : '') +
    `| **Model** | \`${p.model}\` |\n` +
    `| **Markers** | \`${p.markers || 'all LLM tests'}\` |\n` +
    (p.filter ? `| **Filter (-k)** | \`${p.filter}\` |\n` : '') +
    `| **Iterations** | ${p.iterations} |\n` +
    (p.duration ? `| **Duration** | ${p.duration} |\n` : '') +
    `| **Workflow** | ${workflowLinks} |\n`;
}

/**
 * Build comment body based on state
 * @param {Object} p - Parameters object
 * @param {Array<[boolean, string]>} progressSteps - Progress steps (null to hide)
 * @param {Object} extras - Extra options (icon, title, testPreview, context)
 * @returns {string} Markdown body
 */
function buildBody(p, progressSteps, extras = {}) {
  let body = p.isManual
    ? `## ${extras.icon || '🚀'} ${extras.title || 'Manual Eval Running...'}\n\n` +
      renderParamsTable(p, extras.context)
    : `## ${extras.icon || '⏳'} ${extras.title || 'HolmesGPT evals running...'}\n\n` +
      `Automatically triggered by ${p.trigger}\n\n` +
      `[View workflow logs](${p.runUrl})\n`;

  // Only show progress if steps provided (null = hide for completed runs)
  if (progressSteps) {
    body += `\n**Progress:**\n${renderProgress(progressSteps)}\n`;
  }

  if (extras.testPreview) {
    body += `\n<details>\n<summary>📋 Evals to run</summary>\n\n\`\`\`\n${extras.testPreview}\n\`\`\`\n</details>\n`;
  }

  return body;
}

/**
 * Format comma-separated items as code-styled list
 * @param {string} items - Comma-separated items
 * @returns {string} Formatted items
 */
function formatAsCodes(items) {
  if (!items) return '_(loading...)_';
  return items.split(',').map(item => {
    const trimmed = item.trim();
    if (!trimmed) return '';
    return `\`${trimmed}\``;
  }).filter(Boolean).join(', ');
}

/**
 * Build re-run instructions footer for automatic runs
 * @param {Object} p - Parameters object with validMarkers
 * @param {Object} context - GitHub context object
 * @param {Object} options - Options (includeLegend: boolean)
 * @returns {string} Markdown footer
 */
function buildRerunFooter(p, context, options = {}) {
  const { includeLegend = false } = options;
  const repoFullName = `${context.repo.owner}/${context.repo.repo}`;
  const baseWorkflowUrl = `https://github.com/${repoFullName}/actions/workflows/eval-regression.yaml`;
  const workflowUrl = p.displayBranch ? `${baseWorkflowUrl}?ref=${encodeURIComponent(p.displayBranch)}` : baseWorkflowUrl;

  // Format markers as comma-separated code-styled names
  const markersFormatted = formatAsCodes(p.validMarkers);

  // gh CLI command to run workflow from PR branch (include empty filter= for easy copy-paste)
  const ghCommand = p.displayBranch
    ? `gh workflow run eval-regression.yaml --repo ${repoFullName} --ref ${p.displayBranch} -f markers=regression -f filter=`
    : `gh workflow run eval-regression.yaml --repo ${repoFullName} -f markers=regression -f filter=`;

  let footer = '';

  // Only show legend when results are displayed
  if (includeLegend) {
    footer += '\n<details>\n<summary>📖 <b>Legend</b></summary>\n\n' +
      '| Icon | Meaning |\n|------|--------|\n' +
      '| ✅ | The test was successful |\n' +
      '| ➖ | The test was skipped |\n' +
      '| ⚠️ | The test failed but is known to be flaky or known to fail |\n' +
      '| 🚧 | The test had a setup failure (not a code regression) |\n' +
      '| 🔧 | The test failed due to mock data issues (not a code regression) |\n' +
      '| 🚫 | The test was throttled by API rate limits/overload |\n' +
      '| ❌ | The test failed and should be fixed before merging the PR |\n' +
      '</details>\n';
  }

  footer += '\n<details>\n<summary>🔄 <b>Re-run evals manually</b></summary>\n\n' +
    '> ⚠️ **Warning:** `/eval` comments always run using the **workflow from master**, not from this PR branch. ' +
    'If you modified the GitHub Action (e.g., added secrets or env vars), those changes won\'t take effect.\n>\n' +
    '> **To test workflow changes**, use the GitHub CLI or [Actions UI](' + workflowUrl + ') instead:\n>\n' +
    '> ```\n> ' + ghCommand + '\n> ```\n\n' +
    '---\n\n' +
    '**Option 1: Comment on this PR** with `/eval`:\n\n' +
    '```\n/eval\nmarkers: regression\n```\n\n' +
    'Or with more options (one per line):\n\n' +
    '```\n/eval\nmodel: gpt-4o\nmarkers: regression\nfilter: 09_crashpod\niterations: 5\n```\n\n' +
    'Run evals on a different branch (e.g., master) for comparison:\n\n' +
    '```\n/eval\nbranch: master\nmarkers: regression\n```\n\n' +
    '| Option | Description |\n|--------|-------------|\n' +
    '| `model` | Model(s) to test (default: same as automatic runs) |\n' +
    '| `markers` | Pytest markers (**no default - runs all tests!**) |\n' +
    '| `filter` | Pytest -k filter (use `/list` to see valid eval names) |\n' +
    '| `iterations` | Number of runs, max 10 |\n' +
    '| `branch` | Run evals on a different branch (for cross-branch comparison) |\n\n' +
    '**Quick re-run:** Use `/rerun` to re-run the most recent `/eval` on this PR with the same parameters.\n\n' +
    `**Option 2: [Trigger via GitHub Actions UI](${workflowUrl})** → "Run workflow"\n</details>\n` +
    '\n<details>\n<summary>🏷️ <b>Valid markers</b></summary>\n\n' +
    markersFormatted +
    '\n</details>\n' +
    '\n---\n**Commands:** `/eval` · `/rerun` · `/list`\n\n' +
    '**CLI:** `' + ghCommand + '`\n';

  return footer;
}

module.exports = {
  AUTO_EVAL_COMMENT_IDENTIFIER,
  buildParams,
  renderProgress,
  renderParamsTable,
  buildBody,
  buildRerunFooter,
  parseRunHistory,
  extractCurrentRun,
  buildAutoCommentWithHistory
};
