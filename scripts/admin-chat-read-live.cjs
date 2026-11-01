const {readFileSync} = require('node:fs');
const {createRequire} = require('node:module');
const {resolve} = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const req = createRequire(resolve('apps/admin-web/package.json'));
const ts = req('typescript'), vue = req('vue');
const file = readFileSync(resolve('apps/admin-web/src/views/chat/index.vue'), 'utf8');
const script = file.match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1];
const ast = ts.createSourceFile('chat.ts', script, ts.ScriptTarget.Latest, true);
const code = ts.transpileModule([
  ...ast.statements.filter(statement => !ts.isImportDeclaration(statement)).map(statement => statement.getText(ast)),
  'module.exports = { openSession, messages, current }',
].join('\n'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const fixture = JSON.parse(input);
  const request = async (url, token, data) => {
    const response = await fetch(fixture.base + url, {method: data === undefined ? 'GET' : 'POST',
      headers: {Authorization:`Bearer ${token}`, 'Content-Type':'application/json'},
      body: data === undefined ? undefined : JSON.stringify(data)});
    const body = await response.json(); assert.equal(body.code,0,JSON.stringify(body)); return body;
  };
  const first = (await request('/api/chat/send/',fixture.bossToken,{content:'首条消息'})).data;
  let second;
  const chatApi = {
    messages: id => request(`/api/admin/chat-sessions/${id}/messages/`,fixture.adminToken),
    readIds: async (id, ids) => {
      assert.deepEqual([...ids],[first.id]);
      second = (await request('/api/chat/send/',fixture.bossToken,{content:'读取后到达'})).data;
      return request(`/api/admin/chat-sessions/${id}/read/`,fixture.adminToken,{message_ids:ids});
    },
  };
  const module = {exports:{}};
  vm.runInNewContext(code,{module,exports:module.exports,ref:vue.ref,watch:vue.watch,nextTick:vue.nextTick,
    onMounted(){},onUnmounted(){},useAuthStore:()=>({hasPerm:()=>true}),useRouter:()=>({push(){}}),chatApi,
    formatDateTime:String,adminWs:{},ElMessage:{},Picture:{},FormData,document:{hidden:false}});
  const page = module.exports;
  await page.openSession({id:fixture.sessionId,unread_support:1});
  assert.equal(page.messages.value.at(-1).id,first.id);
  assert.ok(second.id > first.id);
  const result = await request('/api/admin/chat-sessions/?only_unread=1',fixture.adminToken);
  assert.equal(result.data.list[0].unread_support,1);
  const messages = await request(`/api/admin/chat-sessions/${fixture.sessionId}/messages/`,fixture.adminToken);
  assert.equal(messages.data.list.find(row=>row.id===second.id).is_read,false);
  console.log('PASS real HTTP staff bounded chat read');
});
