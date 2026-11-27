// Fila online de fim de ano. Browser isolado, API interceptada: nunca usa
// credenciais nem serviços reais.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {readFile} = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');
let browser;
const base = 'http://localhost:8766';
before(async () => {
  browser = await chromium.launch({headless:true,
    ...(process.env.TEST_BROWSER ? {executablePath:process.env.TEST_BROWSER} : {})});
});
after(async () => { if(browser) await browser.close(); });

// respostas: { 'GET /api/x': json | (req,url)=>json | {status, json} }
async function pagina(t, arquivo, respostas = {}, antes) {
  const context = await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
  t.after(() => context.close());
  if (antes) await context.addInitScript(antes);
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const requisicoes = [];
  await context.route('**/*', async route => {
    const req = route.request();
    const u = new URL(req.url());
    if(u.pathname.startsWith('/api/')) {
      let body = null;
      try { body = req.postDataJSON(); } catch { body = null; }
      requisicoes.push({url:u, method:req.method(), body});
      const chave = `${req.method()} ${u.pathname}`;
      let r = respostas[chave];
      if (typeof r === 'function') r = r(req, u);
      if (r && r.__status) return route.fulfill({status:r.__status, json:r.json || {}});
      return route.fulfill({json: r === undefined ? [] : r});
    }
    if(u.origin!==base) return route.fulfill({status:204});
    const name = u.pathname === '/' ? 'index.html' : u.pathname.slice(1);
    const root = path.resolve(__dirname,'..');
    const file = path.resolve(root,name);
    if(!file.startsWith(root+path.sep)) return route.fulfill({status:404});
    try {
      const type = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'}[path.extname(file)] || 'application/octet-stream';
      return route.fulfill({body:await readFile(file),contentType:type});
    } catch { return route.fulfill({status:404}); }
  });
  await page.goto(`${base}/${arquivo}`);
  return {page, requisicoes};
}

function comoAdmin(papel) {
  return `localStorage.setItem('admin_token','x'); localStorage.setItem('admin_papel','${papel}');`;
}

// ---------------------------------------------------------------- dias de fila no painel

test('dias de fila: master cria período', async t => {
  const {page, requisicoes} = await pagina(t, 'admin.html', {
    'GET /api/admin/expedientes': {fechado:true},
    'GET /api/admin/periodos-fila': [],
    'POST /api/admin/periodos-fila': {id:1},
  }, comoAdmin('master'));
  await page.evaluate(() => renderExpediente());
  await page.getByText('Dias de fila', {exact:true}).waitFor();
  await page.locator('#fila-inicio').fill('2099-12-21');
  await page.locator('#fila-fim').fill('2099-12-31');
  await page.getByRole('button', {name:'Salvar dias de fila'}).click();
  await page.waitForFunction(() => true);
  await new Promise(r => setTimeout(r, 300));
  const post = requisicoes.find(r => r.method === 'POST' && r.url.pathname === '/api/admin/periodos-fila');
  assert.ok(post, 'deveria ter enviado o período');
  assert.deepEqual(post.body, {inicio:'2099-12-21', fim:'2099-12-31'});
});

test('dias de fila: lista o período e apaga com confirmação na página', async t => {
  const {page, requisicoes} = await pagina(t, 'admin.html', {
    'GET /api/admin/expedientes': {fechado:true},
    'GET /api/admin/periodos-fila': [{id:4, inicio:'2099-12-21', fim:'2099-12-31'}],
    'DELETE /api/admin/periodos-fila/4': {mensagem:'ok'},
  }, comoAdmin('master'));
  await page.evaluate(() => renderExpediente());
  await page.getByText('21/12 a 31/12').waitFor();
  await page.getByRole('button', {name:'Apagar'}).click();
  assert.equal(requisicoes.filter(r => r.method === 'DELETE').length, 0, 'primeiro toque só pede confirmação');
  await page.getByRole('button', {name:'Confirmar'}).click();
  await new Promise(r => setTimeout(r, 300));
  assert.equal(requisicoes.filter(r => r.method === 'DELETE').length, 1);
});

test('dias de fila: barbeiro não vê o cartão', async t => {
  const {page} = await pagina(t, 'admin.html', {
    'GET /api/admin/expedientes': {fechado:true},
    'GET /api/admin/periodos-fila': [],
  }, comoAdmin('barbeiro'));
  await page.evaluate(() => renderExpediente());
  await new Promise(r => setTimeout(r, 300));
  assert.equal(await page.getByText('Dias de fila', {exact:true}).count(), 0);
});

// ---------------------------------------------------------------- site: dia de fila

test('site: dia de fila mostra o caminho da fila', async t => {
  const todosDeFila = (req, u) => {
    const inicio = new Date(u.searchParams.get('inicio') + 'T12:00:00');
    const dias = {};
    for (let i = 0; i < Number(u.searchParams.get('dias')); i++) {
      const d = new Date(inicio); d.setDate(inicio.getDate() + i);
      dias[d.toISOString().slice(0,10)] = {horarios_disponiveis: [], modo_fila: true};
    }
    return {barbeiro_id: 2, dias};
  };
  const {page} = await pagina(t, 'index.html', {
    'GET /api/servicos': [{id:1, nome:'Corte', preco:40, duracao_min:30}],
    'GET /api/barbeiros': [{id:2, nome:'Rian'}],
    'GET /api/horarios-disponiveis-periodo': todosDeFila,
  });
  await page.locator('.card-opcao', {hasText:'Corte'}).first().click();
  await page.locator('.card-opcao', {hasText:'Rian'}).first().click();
  await page.getByText('Nesse dia o atendimento é por ordem de chegada').waitFor();
  const link = page.getByRole('link', {name:'Entrar na fila'});
  assert.equal(await link.getAttribute('href'), 'fila.html');
});
