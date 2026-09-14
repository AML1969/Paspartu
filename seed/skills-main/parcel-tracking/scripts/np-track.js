#!/usr/bin/env node
// Nova Post Global tracking — direct API (personal.novaposhtaglobal.ua/tracking.php), no browser.
// Contract preserved for hermes cron: JSON output + "=== STATUS_CHANGED ===" marker.
const { execFileSync } = require('child_process');
const fs = require('fs');

const trackingId = process.argv[2] || 'NP80000004830699NPG';
const stateFile = `/root/hermes-workspace/.np-track-${trackingId}.json`;

try {
  const raw = execFileSync('curl', [
    '-s', '-m', '25',
    'https://personal.novaposhtaglobal.ua/tracking.php',
    '-F', `num=${trackingId}`,
    '-F', 'lang=ua'
  ], { encoding: 'utf8' });

  const data = JSON.parse(raw);
  if (!data || typeof data !== 'object' || !Array.isArray(data.historyStatus)) {
    console.log('ERROR: unexpected API response: ' + String(raw).slice(0, 200));
    process.exit(1);
  }

  const statuses = data.historyStatus.map(s => ({
    description: `${s.status}${s.location ? ' [' + s.location + ']' : ''}`,
    datetime: s.date,
    code: s.code
  }));
  const latest = statuses[0] || { description: 'UNKNOWN', datetime: 'UNKNOWN' };

  let prevLatest = null;
  try { prevLatest = JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch (e) {}
  fs.writeFileSync(stateFile, JSON.stringify(latest));

  const output = {
    tracking: data.billNumber,
    route: `${data.from} -> ${data.to}`,
    received: data.isRecive,
    latest,
    previous: prevLatest,
    changed: prevLatest ? (latest.datetime !== prevLatest.datetime || latest.description !== prevLatest.description) : true,
    all_statuses: statuses
  };
  console.log(JSON.stringify(output, null, 2));
  if (output.changed && prevLatest) console.log('=== STATUS_CHANGED ===');
} catch (e) {
  console.log('ERROR: ' + e.message);
  process.exit(1);
}
