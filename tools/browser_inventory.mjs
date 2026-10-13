// Observe emitted modules without modifying the browser bundles.
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync} from 'node:fs';
import path from 'node:path';

export const digest = bytes => createHash('sha256').update(bytes).digest('hex');

function packageRecord(folder, output) {
  const bytes = readFileSync(path.join(folder, 'package.json'));
  const metadata = JSON.parse(bytes);
  if (typeof metadata.name !== 'string' || !metadata.name || typeof metadata.version !== 'string' || !metadata.version) {
    throw new Error('Package identity is missing: ' + folder);
  }
  const notices = [];
  function retain(source, kind) {
    const content = readFileSync(path.join(folder, source));
    const sha256 = digest(content);
    const file = sha256 + '.txt';
    mkdirSync(path.join(output, 'notices'), {recursive: true});
    writeFileSync(path.join(output, 'notices', file), content);
    notices.push({source, kind, file: 'notices/' + file, sha256});
  }
  for (const entry of readdirSync(folder, {withFileTypes: true})) {
    if (!entry.isFile() || !/^(licen[cs]e|copying|copyright|notice|third.?party)/i.test(entry.name)) continue;
    retain(entry.name, 'notice');
  }
  // Some publishers put their notice in the entry source rather than a
  // separate file. Preserve that source unchanged for review, without
  // interpreting its comments or synthesizing a license declaration.
  if (!notices.length) {
    for (const source of [metadata.module, metadata.main]) {
      if (typeof source !== 'string') continue;
      const selected = path.resolve(folder, source);
      if (!existsSync(selected) || !statSync(selected).isFile()) continue;
      if (!realpathSync(selected).startsWith(realpathSync(folder) + path.sep)) {
        throw new Error('Package entry is outside its directory: ' + folder);
      }
      retain(source, 'entry_source');
      break;
    }
  }
  return {name: metadata.name, version: metadata.version, declared_license: metadata.license,
          metadata_sha256: digest(bytes), notices};
}

export function inventory(root, output, kind) {
  const records = new Map();
  return {
    name: 'openavc-browser-inventory',
    enforce: 'post',
    generateBundle: {
      order: 'post',
      handler(options, bundle) {
        const chunks = [], packages = {};
        for (const item of Object.values(bundle)) {
          if (item.type !== 'chunk') continue;
          const modules = [];
          for (const [id, info] of Object.entries(item.modules)) {
            if (info.renderedLength <= 0) continue;
            const module = {id: id.replaceAll(root + '/', ''), rendered_length: info.renderedLength};
            const marker = id.lastIndexOf('/node_modules/');
            if (marker >= 0) {
              const parts = id.slice(marker + '/node_modules/'.length).split('/');
              const name = parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
              const folder = id.slice(0, marker + '/node_modules/'.length).replace(/^\0/, '') + name;
              if (!records.has(folder)) records.set(folder, packageRecord(folder, output));
              const record = records.get(folder);
              const key = `${record.name}@${record.version}`;
              packages[key] = record;
              module.package = key;
            }
            modules.push(module);
          }
          chunks.push({file: item.fileName, sha256: digest(item.code), modules});
        }
        const bytes = JSON.stringify({kind, chunks, packages}, null, 2) + '\n';
        mkdirSync(path.join(output, 'observations'), {recursive: true});
        writeFileSync(path.join(output, 'observations', digest(bytes) + '.json'), bytes);
      },
    },
  };
}

export function copiedAsset(source, target, packageFolder, output) {
  const original = readFileSync(source), copied = readFileSync(target);
  if (!original.equals(copied)) throw new Error('The copied browser asset differs from its source: ' + target);
  return {sha256: digest(copied), package: packageRecord(packageFolder, output)};
}
