import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {copiedAsset, digest, inventory} from '../tools/browser_inventory.mjs';

test('emitted modules identify their package and preserve original notices', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'openavc-browser-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const folder = path.join(root, 'node_modules/@example/widgets');
  const output = path.join(root, 'records');
  mkdirSync(folder, {recursive: true});
  const metadata = JSON.stringify({name: '@example/widgets', version: '3.2.1', license: 'MIT'});
  const notice = 'Original publisher notice\r\n';
  writeFileSync(path.join(folder, 'package.json'), metadata);
  writeFileSync(path.join(folder, 'LICENSE'), notice);
  const observer = inventory(root, output, 'worker');
  observer.generateBundle.handler({}, {
    'worker.js': {type: 'chunk', fileName: 'worker.js', code: 'console.log(1);', modules: {
      [path.join(folder, 'index.js')]: {renderedLength: 15},
      [path.join(root, 'node_modules/absent/unused.js')]: {renderedLength: 0},
    }},
  });
  const entries = readdirSync(path.join(output, 'observations'));
  assert.equal(entries.length, 1);
  const record = JSON.parse(readFileSync(path.join(output, 'observations', entries[0]), 'utf8'));
  assert.equal(record.kind, 'worker');
  assert.deepEqual(Object.keys(record.packages), ['@example/widgets@3.2.1']);
  const component = record.packages['@example/widgets@3.2.1'];
  assert.equal(component.metadata_sha256, digest(metadata));
  assert.equal(component.declared_license, 'MIT');
  assert.equal(readFileSync(path.join(output, component.notices[0].file), 'utf8'), notice);
  assert.deepEqual(record.chunks, [{file: 'worker.js', sha256: digest('console.log(1);'), modules: [
    {id: 'node_modules/@example/widgets/index.js', rendered_length: 15, package: '@example/widgets@3.2.1'},
  ]}]);
  const source = path.join(root, 'sprite.svg'), target = path.join(root, 'copied.svg');
  writeFileSync(source, '<svg/>');
  writeFileSync(target, '<svg/>');
  assert.equal(copiedAsset(source, target, folder, output).sha256, digest('<svg/>'));
  writeFileSync(target, '<svg>changed</svg>');
  assert.throws(() => copiedAsset(source, target, folder, output), /differs from its source/);
  writeFileSync(path.join(folder, 'package.json'), '{}');
  writeFileSync(target, '<svg/>');
  assert.throws(() => copiedAsset(source, target, folder, output), /identity is missing/);
  rmSync(path.join(folder, 'LICENSE'));
  writeFileSync(path.join(folder, 'entry.mjs'), '// Publisher notice\nexport default 1;\n');
  writeFileSync(path.join(folder, 'package.json'), JSON.stringify({name: '@example/widgets', version: '3.2.1', module: 'entry.mjs'}));
  const retained = copiedAsset(source, target, folder, output).package.notices;
  assert.equal(retained.length, 1);
  assert.equal(retained[0].source, 'entry.mjs');
  assert.equal(retained[0].kind, 'entry_source');
  assert.equal(readFileSync(path.join(output, retained[0].file), 'utf8'), '// Publisher notice\nexport default 1;\n');
  writeFileSync(path.join(root, 'outside.mjs'), '// Not part of this package.\n');
  writeFileSync(path.join(folder, 'package.json'), JSON.stringify({name: '@example/widgets', version: '3.2.1', module: '../../../outside.mjs'}));
  assert.throws(() => copiedAsset(source, target, folder, output), /outside its directory/);
});
