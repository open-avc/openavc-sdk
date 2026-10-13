// Run the core's normal build and retain observed package-to-output attribution.
import {execFileSync} from 'node:child_process';
import {existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {copiedAsset, digest} from './browser_inventory.mjs';

const [rootArgument, outputArgument] = process.argv.slice(2);
if (!rootArgument || !outputArgument) throw new Error('Use build_browser.mjs <interface directory> <new report directory>.');
const root = path.resolve(rootArgument), output = path.resolve(outputArgument);
if (existsSync(output)) throw new Error('Select a new browser build report directory.');
mkdirSync(output, {recursive: true});
const plugin = path.join(path.dirname(fileURLToPath(import.meta.url)), 'browser_inventory.mjs');
const config = path.join(root, 'openavc-inventory.config.mjs');
const observer = `inventory(${JSON.stringify(root)}, ${JSON.stringify(output)}, `;
const wrapper = `import {defineConfig} from 'vite';
import original from './vite.config.ts';
import {inventory} from ${JSON.stringify(plugin)};
export default defineConfig(async env => {
  const base = await (typeof original === 'function' ? original(env) : original);
  return {...base, plugins: [...(base.plugins || []), ${observer}'main')],
    worker: {...base.worker, plugins: () => [...(base.worker?.plugins?.() || []), ${observer}'worker')]}};
});\n`;
writeFileSync(config, wrapper, {flag: 'wx'});
try {
  execFileSync('npm', ['run', 'build', '--', '--config', config], {cwd: root, stdio: 'inherit'});
} finally {
  rmSync(config);
}

function files(directory, relative = '') {
  return Object.fromEntries(readdirSync(path.join(directory, relative)).sort().flatMap(name => {
    const file = path.join(relative, name), full = path.join(directory, file);
    return statSync(full).isDirectory() ? Object.entries(files(directory, file)) : [[file, digest(readFileSync(full))]];
  }));
}

const observations = readdirSync(path.join(output, 'observations')).sort().map(name =>
  JSON.parse(readFileSync(path.join(output, 'observations', name), 'utf8')));
const emitted = files(path.join(root, 'dist'));
const chunks = observations.flatMap(record => record.chunks);
if (!chunks.length) throw new Error('The browser build produced no observed chunks.');
for (const chunk of chunks) {
  if (emitted[chunk.file] !== chunk.sha256) throw new Error('Observed chunk differs from its emitted file: ' + chunk.file);
}
const unobserved = Object.keys(emitted).filter(name => name.endsWith('.js') && !chunks.some(chunk => chunk.file === name));
if (unobserved.length) throw new Error('JavaScript files lack build attribution: ' + unobserved.join(', '));
const inputs = Object.fromEntries(['package.json', 'package-lock.json', 'vite.config.ts'].map(name =>
  [name, digest(readFileSync(path.join(root, name)))]));
const assets = {};
if (path.basename(root) === 'programmer') {
  assets['web/panel/icons.svg'] = copiedAsset(path.join(root, 'node_modules/lucide-static/sprite.svg'),
    path.join(root, '../panel/icons.svg'), path.join(root, 'node_modules/lucide-static'), output);
}
const require = createRequire(path.join(root, 'package.json'));
const tools = Object.fromEntries(['vite', 'rollup', 'typescript'].map(name => {
  const bytes = readFileSync(require.resolve(name + '/package.json'));
  return [name, {version: JSON.parse(bytes).version, metadata_sha256: digest(bytes)}];
}));
const report = {
  coverage: 'Packages contributing positive rendered module length to observed Vite/Rollup output, including workers, '
    + 'plus explicitly compared copied assets. Other assets, generated helpers, nested vendored components, license '
    + 'interpretation and publisher authentication are not established by this report.',
  node: process.version, tools, inputs, outputs: emitted, copied_assets: assets,
  packages: Object.assign({}, ...observations.map(record => record.packages)),
  observations,
};
writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
