import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {resolveConnection} from '../../tools/_comfyui/node_connection.mjs';

test('registered environment works independent of cwd and does not leak key to other hosts',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'h3-connection-'));
 try {
  fs.writeFileSync(path.join(dir,'private.env'),'UNRELATED_SECRET=do-not-load\nCOMFY_URL=https://h3.example\nCOMFY_TOKEN=test-key\n');
  const file=path.join(dir,'connection.json');
  fs.writeFileSync(file,JSON.stringify({env_file:'private.env'}));
  assert.deepEqual(resolveConnection(undefined,undefined,file),{base:'https://h3.example',token:'test-key'});
  assert.deepEqual(resolveConnection('https://other.example',undefined,file),{base:'https://other.example',token:undefined});
  assert.deepEqual(resolveConnection('https://other.example','explicit-key',file),{base:'https://other.example',token:'explicit-key'});
  assert.equal(process.env.UNRELATED_SECRET,undefined);
 } finally { fs.rmSync(dir,{recursive:true}); }
});

test('missing registration preserves explicit connection; missing registered secret fails clearly',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'h3-connection-'));
 try {
  const file=path.join(dir,'connection.json');
  assert.deepEqual(resolveConnection('http://localhost:8188',undefined,file),{base:'http://localhost:8188',token:undefined});
  fs.writeFileSync(file,JSON.stringify({env_file:'absent.env'}));
  assert.throws(()=>resolveConnection(undefined,undefined,file),/registered H3 environment file/);
 } finally { fs.rmSync(dir,{recursive:true}); }
});
