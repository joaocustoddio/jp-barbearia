// Browser isolado, API interceptada: nunca usa credenciais nem serviços reais.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {readFile,mkdir} = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');
let browser;
const base = 'http://localhost:8765';
before(async () => {
  if(process.env.TEST_SCREENSHOT_DIR) await mkdir(process.env.TEST_SCREENSHOT_DIR,{recursive:true});
  browser = await chromium.launch({headless:true,
    ...(process.env.TEST_BROWSER ? {executablePath:process.env.TEST_BROWSER} : {})});
});
after(async () => { if(browser) await browser.close(); });

async function pagina(t, arquivo='admin.html') {
  const context = await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
  t.after(() => context.close());
  const page = await context.newPage();
  const requisicoes = [];
  await context.route('**/*', async route => {
    const u = new URL(route.request().url());
    if(u.pathname.startsWith('/api/')) {
      const req = route.request();
      requisicoes.push({url:u,method:req.method(),body:req.postDataJSON()});
      let json = [];
      if(u.pathname === '/api/servicos') json=[{id:1,nome:'Corte',preco:u.searchParams.get('data')==='2099-11-03'?60:40,duracao_min:50}];
      if(u.pathname === '/api/barbeiros') json=[{id:2,nome:'Rian'}];
      if(u.pathname === '/api/horarios-disponiveis') json={horarios_disponiveis:['09:00','10:00']};
      if(u.pathname === '/api/agendamentos' && req.method()==='POST') json={agendamento_id:1,preco:60};
      if(req.method()==='PATCH'||req.method()==='PUT') json={mensagem:'Salvo'};
      return route.fulfill({json});
    }
    if(u.origin!==base) return route.fulfill({status:204});
    const name = u.pathname === '/'?'index.html':u.pathname.slice(1);
    const root = path.resolve(__dirname,'..');
    const file = path.resolve(root,name);
    if(!file.startsWith(root+path.sep)) return route.fulfill({status:404});
    try {
      const type = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'}[path.extname(file)] || 'application/octet-stream';
      return route.fulfill({body:await readFile(file),contentType:type});
    } catch { return route.fulfill({status:404}); }
  });
  await page.goto(`${base}/${arquivo}`);
  return {page,requisicoes};
}
const ag = {id:1,cliente_nome:'Sérgio',servico_nome:'Corte',servico_id:1,barbeiro_id:2,servico_preco:40,servico_duracao:50,hora:'09:00',data:'2099-11-03',cliente_telefone:'11999999999'};

test('menu Alterar salva desconto e duração apenas no agendamento', async t => {
  const {page,requisicoes}=await pagina(t);
  await page.evaluate(a => {localStorage.setItem('admin_papel','barbeiro'); abrirMenuCard({clientX:100,clientY:100},a,a.data);},ag);
  await page.getByRole('button',{name:'Alterar',exact:true}).click();
  await page.locator('[name=preco]').fill('20,00');
  await page.locator('[name=duracao]').fill('20');
  if(process.env.TEST_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TEST_SCREENSHOT_DIR,'alterar-mobile.png')});
  await page.getByRole('button',{name:'Salvar',exact:true}).click();
  await page.locator('.modal-overlay').waitFor({state:'detached'});
  const patch=requisicoes.find(r=>r.method==='PATCH');
  assert.equal(patch.url.pathname,'/api/admin/agendamentos/1');
  assert.deepEqual(patch.body,{preco:20,duracao_min:20});
});

test('editar só duração não fixa preço manual',async t=>{
  const {page,requisicoes}=await pagina(t);
  await page.evaluate(a=>abrirModalAlterar(a),ag);
  await page.locator('[name=duracao]').fill('20');
  await page.getByRole('button',{name:'Salvar',exact:true}).click();
  await page.locator('.modal-overlay').waitFor({state:'detached'});
  assert.deepEqual(requisicoes.find(r=>r.method==='PATCH').body,{duracao_min:20});
});

test('conflito mantém formulário aberto com mensagem e permite corrigir',async t=>{
  const {page}=await pagina(t);
  await page.route('**/api/admin/agendamentos/1',route=>route.fulfill({status:409,json:{erro:'A nova duração invade outro atendimento.'}}));
  await page.evaluate(a=>abrirModalAlterar(a),ag);
  await page.locator('[name=duracao]').fill('80');
  await page.getByRole('button',{name:'Salvar',exact:true}).click();
  await page.getByRole('alert').getByText('A nova duração invade outro atendimento.').waitFor();
  assert.equal(await page.locator('.modal-overlay').count(),1);
  assert.equal(await page.getByRole('button',{name:'Salvar',exact:true}).isEnabled(),true);
});

test('salão não recebe ação de alterar valores',async t=>{
  const {page}=await pagina(t);
  await page.evaluate(a=>{localStorage.setItem('admin_papel','salao');abrirMenuCard({clientX:100,clientY:100},a,a.data);},ag);
  assert.equal(await page.getByRole('button',{name:'Alterar',exact:true}).count(),0);
});

test('repetição mostra preço da data e atualiza ao trocar a data',async t=>{
  const {page}=await pagina(t);
  await page.evaluate(a=>abrirModalRepetir(a,'2099-11-03'),ag);
  assert.match(await page.locator('#rep-servico').textContent(),/60,00/);
  await page.locator('#rep-data').fill('2099-10-31');
  await page.locator('#rep-data').dispatchEvent('change');
  await page.waitForFunction(()=>document.querySelector('#rep-servico').textContent.includes('40,00'));
});

test('reajuste exige confirmação e envia vigência escolhida',async t=>{
  const {page,requisicoes}=await pagina(t);
  await page.evaluate(()=>{document.querySelector('#tela-login').hidden=true;document.querySelector('#tela-painel').hidden=false;localStorage.setItem('admin_papel','master');renderPrecos();});
  await page.locator('.preco-valor').first().fill('60,00');
  await page.locator('.preco-vigencia').fill('2099-11-03');
  if(process.env.TEST_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TEST_SCREENSHOT_DIR,'reajuste-mobile.png'),fullPage:true});
  let confirmou=false;
  page.once('dialog',async d=>{confirmou=true; assert.match(d.message(),/03\/11\/2099/); await d.accept();});
  await Promise.all([
    page.waitForResponse(r=>r.request().method()==='PUT'),
    page.locator('#precos-servicos .preco-salvar').click()
  ]);
  assert.equal(confirmou,true);
  assert.deepEqual(requisicoes.find(r=>r.method==='PUT').body,{preco:60,duracao_min:50,inicio_vigencia:'2099-11-03'});
});

test('cliente vê preço da data antes de confirmar e recebe a confirmação',async t=>{
  const {page,requisicoes}=await pagina(t,'index.html');
  await page.evaluate(async()=>{state.servico={id:1,nome:'Corte',preco:40,duracao_min:50};state.barbeiro={id:2,nome:'Rian'};state.data='2099-11-03';await renderPassoHorario();});
  await page.getByRole('button',{name:'09:00',exact:true}).click();
  assert.match(await page.locator('#step-content').textContent(),/60,00/);
  await page.locator('#input-nome').fill('Sérgio');
  await page.locator('#input-telefone').fill('11999999999');
  await page.getByRole('button',{name:'Confirmar agendamento'}).click();
  // A tela de sucesso é só a confirmação: o código assinado saiu em 21/10/2026
  // porque obrigava o cliente a guardar 150 caracteres pra cancelar um corte.
  await page.getByRole('button',{name:'OK'}).waitFor();
  assert.match(await page.locator('#step-content').textContent(),/Agendamento confirmado/);
  assert.equal(await page.locator('textarea').count(),0);
  assert.ok(requisicoes.some(r=>r.method==='POST'));
});

// A imagem do story leva alguns segundos pra buscar os horários de todos os
// barbeiros. Quando passa dos ~5s o navegador recusa abrir o menu de
// compartilhar ("gesto expirado") — e o clique não pode terminar em nada.
async function clicarGerarStory(page, erroDoShare) {
  await page.evaluate(e => {
    navigator.canShare = () => true;
    navigator.share = () => Promise.reject(Object.assign(new Error('x'), {name: e}));
  }, erroDoShare);
  const baixou = page.waitForEvent('download', {timeout: 2500}).then(d => d.suggestedFilename(), () => null);
  const fim = await page.evaluate(() => gerarStory('2099-11-03'));
  return {fim, baixou: await baixou};
}

test('share recusado pelo navegador cai no download', async t => {
  const {page} = await pagina(t);
  const {fim, baixou} = await clicarGerarStory(page, 'NotAllowedError');
  assert.equal(fim, 'baixado');
  assert.equal(baixou, 'horarios-2099-11-03.png');
});

test('share fechado pelo usuário não baixa por cima', async t => {
  const {page} = await pagina(t);
  const {fim, baixou} = await clicarGerarStory(page, 'AbortError');
  assert.equal(fim, 'cancelado');
  assert.equal(baixou, null);
});
