// Read only H3 service credentials; never import unrelated provider keys into process.env.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

export function resolveConnection(base, token, configFile) {
  const configPath = configFile instanceof URL ? fileURLToPath(configFile) : configFile;
  let config;
  try { config = JSON.parse(fs.readFileSync(configPath, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return {base, token}; throw error; }
  if (typeof config.env_file !== 'string') throw Error('H3 connection registration requires env_file');
  let contents;
  try { contents = fs.readFileSync(path.resolve(path.dirname(configPath), config.env_file), 'utf8'); }
  catch { throw Error('Cannot read registered H3 environment file; ask the service maintainer to restore it'); }
  const values = {};
  for (const line of contents.split(/\r?\n/)) {
    const match = line.match(/^\s*(COMFY_URL|COMFY_TOKEN)\s*=\s*(.*?)\s*$/);
    if (!match) continue;
    let value = match[2];
    if (value.startsWith('"') || value.startsWith("'")) value = value.slice(1, value.lastIndexOf(value[0]));
    else value = value.replace(/\s+#.*$/, '').trim();
    values[match[1]] = value;
  }
  const selected = base || values.COMFY_URL;
  // A caller can select another service, but that must not inherit the registered token.
  const sameService = selected?.replace(/\/$/, '') === values.COMFY_URL?.replace(/\/$/, '');
  return {base: selected, token: token ?? (sameService ? values.COMFY_TOKEN : undefined)};
}
