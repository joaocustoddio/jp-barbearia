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
  const context = await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',
    timezoneId:'America/Sao_Paulo'});
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

// ---------------------------------------------------------------- página da fila

const HOJE_LOCAL = `(()=>{const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');})()`;
const comToken = (token='tok') =>
  `localStorage.setItem('fila_token', JSON.stringify({token:'${token}', data:${HOJE_LOCAL}}));`;

const filaHoje = {
  dia_de_fila: true,
  barbeiros: [
    {id:1, nome:'JP', foto:null, aberta:false, motivo:'De folga hoje', esperando:0, espera_min:null},
    {id:2, nome:'Rian', foto:null, aberta:true, motivo:null, esperando:2, espera_min:60},
  ],
  qualquer_um: {aberta:true, esperando:1, espera_min:30},
};
const servicos = [{id:1, nome:'Corte', preco:40, duracao_min:30}];
const situacao = (extra) => ({status:'aguardando', posicao:4, a_frente:3, espera_min:35,
  barbeiro:'Rian', servico:'Corte', chamado_em:null, tolerancia_min:10, ...extra});

test('fila: escolher barbeiro e entrar', async t => {
  const {page, requisicoes} = await pagina(t, 'fila.html', {
    'GET /api/fila/hoje': filaHoje, 'GET /api/servicos': servicos,
    'POST /api/fila': {token:'tok', posicao:3, espera_min:60},
    'GET /api/fila/tok': situacao({posicao:3, a_frente:2, espera_min:60}),
  });
  await page.getByText('Rian').first().waitFor();
  await page.getByText('2 esperando · uns 60 min').waitFor();
  await page.getByText('Qualquer um').first().waitFor();
  await page.locator('.cartao-barbeiro', {hasText:'Rian'}).click();
  await page.locator('#fila-nome').fill('Ana');
  await page.locator('#fila-telefone').fill('11988887777');
  await page.getByRole('button', {name:'Entrar na fila'}).click();
  await page.getByText('3º').waitFor();
  const post = requisicoes.find(r => r.method === 'POST' && r.url.pathname === '/api/fila');
  assert.deepEqual(post.body, {nome:'Ana', telefone:'11988887777', servico_id:1, barbeiro_id:2});
  assert.equal(JSON.parse(await page.evaluate(() => localStorage.getItem('fila_token'))).token, 'tok');
});

test('fila: barbeiro de folga aparece apagado', async t => {
  const {page} = await pagina(t, 'fila.html', {'GET /api/fila/hoje': filaHoje, 'GET /api/servicos': servicos});
  const jp = page.locator('.cartao-barbeiro', {hasText:'JP'});
  await jp.waitFor();
  assert.equal(await jp.isDisabled(), true);
  await page.getByText('De folga hoje').waitFor();
});

test('fila: posição e espera de quem já está na fila', async t => {
  const {page} = await pagina(t, 'fila.html', {'GET /api/fila/tok': situacao()}, comToken());
  await page.getByText('4º').waitFor();
  await page.getByText('3 na sua frente · uns 35 min').waitFor();
  assert.equal(await page.getByText('Já vem vindo').count(), 0);
});

test('fila: perto mostra "Já vem vindo"', async t => {
  const {page} = await pagina(t, 'fila.html',
    {'GET /api/fila/tok': situacao({posicao:3, a_frente:2, espera_min:20})}, comToken());
  await page.getByText('Já vem vindo').waitFor();
});

test('fila: chamado mostra "É sua vez!"', async t => {
  const {page} = await pagina(t, 'fila.html', {'GET /api/fila/tok': situacao({
    status:'chamado', posicao:1, a_frente:0, chamado_em:'2099-01-01T10:00:00-03:00'})}, comToken());
  await page.getByText('É sua vez!').waitFor();
  await page.getByText('Você tem até 10:10 para chegar').waitFor();
});

test('fila: sair pede confirmação na página', async t => {
  const {page, requisicoes} = await pagina(t, 'fila.html', {
    'GET /api/fila/tok': situacao(), 'POST /api/fila/tok/sair': {mensagem:'ok'},
    'GET /api/fila/hoje': filaHoje, 'GET /api/servicos': servicos,
  }, comToken());
  await page.getByRole('button', {name:'Sair da fila'}).click();
  assert.equal(requisicoes.filter(r => r.method === 'POST').length, 0);
  await page.getByRole('button', {name:'Sim, sair da fila'}).click();
  await page.getByText('Você saiu da fila').waitFor();
  assert.equal(requisicoes.filter(r => r.url.pathname === '/api/fila/tok/sair').length, 1);
});

test('fila: link velho', async t => {
  const {page} = await pagina(t, 'fila.html', {
    'GET /api/fila/velho': {__status:404, json:{erro:'Esse link não vale mais.'}},
  }, comToken('velho'));
  await page.getByText('Esse link não vale mais').waitFor();
  await page.getByRole('button', {name:'Entrar de novo'}).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem('fila_token')), null);
});

test('fila: sem conexão mostra reconectando', async t => {
  const {page} = await pagina(t, 'fila.html', {
    'GET /api/fila/tok': {__status:503, json:{}},
  }, comToken());
  await page.getByText('Reconectando…').waitFor();
});

test('fila: fora do dia', async t => {
  const {page} = await pagina(t, 'fila.html', {
    'GET /api/fila/hoje': {dia_de_fila:false, barbeiros:[], qualquer_um:{aberta:false}},
    'GET /api/servicos': servicos,
  });
  await page.getByText('Hoje o atendimento é com horário marcado').waitFor();
  assert.equal(await page.locator('#fila-nome').count(), 0);
});

test('site: cartão da fila no topo', async t => {
  const {page} = await pagina(t, 'index.html', {
    'GET /api/fila/hoje': filaHoje, 'GET /api/servicos': servicos, 'GET /api/barbeiros': [],
  });
  const link = page.getByRole('link', {name:'Entrar na fila'});
  await link.waitFor();
  assert.equal(await link.getAttribute('href'), 'fila.html');
});
