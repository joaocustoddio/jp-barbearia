/* =====================================================
   admin.js
   Lógica do Painel do Barbeiro (área administrativa).

   Fluxo:
   1. Se não tem token válido → mostra tela de login
   2. Login OK → guarda JWT (via api.js) e mostra o painel
   3. Painel tem 4 abas: Dashboard, Agendamentos, Barbeiros, Horários
   4. Qualquer 401 (token expirado) derruba pra tela de login
   ===================================================== */

/* -------------------- Referências DOM -------------------- */
const telaLogin   = document.getElementById("tela-login");
const telaPainel  = document.getElementById("tela-painel");
const formLogin   = document.getElementById("form-login");
const loginErro   = document.getElementById("login-erro");
const btnLogin    = document.getElementById("btn-login");
const btnLogout   = document.getElementById("btn-logout");
const abas        = document.getElementById("abas");
const elConteudo  = document.getElementById("conteudo-painel");

/* -------------------- Formatação -------------------- */
function formatarMoeda(valor) {
  return (valor || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function formatarDataBR(dataISO) {
  if (!dataISO) return "";
  const [ano, mes, dia] = dataISO.split("-");
  return `${dia}/${mes}/${ano}`;
}

function hojeISO() {
  const d = new Date();
  const mes = String(d.getMonth() + 1).padStart(2, "0");
  const dia = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mes}-${dia}`;
}

/* -------------------- Helpers de UI -------------------- */
function escapeHTML(txt) {
  const div = document.createElement("div");
  div.textContent = txt == null ? "" : String(txt);
  return div.innerHTML;
}

// Primeira letra maiúscula (ex: "confirmado" -> "Confirmado")
function capitalizar(txt) {
  if (!txt) return "";
  return txt.charAt(0).toUpperCase() + txt.slice(1);
}

function carregando(texto = "Carregando...") {
  return `<div class="estado-vazio">${escapeHTML(texto)}</div>`;
}

function vazio(texto) {
  return `<div class="estado-vazio">${escapeHTML(texto)}</div>`;
}

/**
 * Trata erros de chamada admin. Se for 401 (token expirado/ausente),
 * derruba pra tela de login. Caso contrário, devolve a mensagem
 * pra ser exibida na seção.
 */
function tratarErro(erro) {
  if (erro && erro.naoAutorizado) {
    API.admin.logout();
    mostrarLogin();
    loginErro.textContent = "Sua sessão expirou. Entre novamente.";
    return null;
  }
  return erro.message || "Erro inesperado.";
}

/* =====================================================
   LOGIN / LOGOUT
   ===================================================== */
function mostrarLogin() {
  telaPainel.hidden = true;
  telaLogin.hidden = false;
}

function mostrarPainel() {
  telaLogin.hidden = true;
  telaPainel.hidden = false;
  const primeira = montarAbas();   // abas conforme o papel (master vê todas)
  trocarSecao(primeira);
}

async function fazerLogin() {
  loginErro.textContent = "";
  const usuario = document.getElementById("login-usuario").value.trim();
  const senha   = document.getElementById("login-senha").value;

  if (!usuario || !senha) {
    loginErro.textContent = "Preencha usuário e senha.";
    return;
  }

  btnLogin.disabled = true;
  btnLogin.textContent = "Entrando...";

  try {
    await API.admin.login(usuario, senha);
    document.getElementById("login-senha").value = "";
    mostrarPainel();
  } catch (erro) {
    loginErro.textContent = erro.message || "Não foi possível entrar.";
  } finally {
    btnLogin.disabled = false;
    btnLogin.textContent = "Entrar";
  }
}

// Botão é type="button" (não envia o form) → clicar nunca recarrega a página.
btnLogin.addEventListener("click", fazerLogin);

// Mantém o Enter funcionando: intercepta o submit do form e impede o reload.
formLogin.addEventListener("submit", (e) => {
  e.preventDefault();
  fazerLogin();
});

btnLogout.addEventListener("click", () => {
  API.admin.logout();
  mostrarLogin();
});

/* =====================================================
   NAVEGAÇÃO POR ABAS
   ===================================================== */
// Abas por papel:
// - master: tudo (dono)
// - barbeiro: só o dele (agenda + seus valores)
// - salao: tablet compartilhado — agenda de todos + contagem SÓ com quantidade
const ABAS_POR_PAPEL = {
  master: [
    { chave: "dashboard",    rotulo: "Dashboard" },
    { chave: "agendamentos", rotulo: "Agendamentos" },
    { chave: "contagem",     rotulo: "Contagem" },
    { chave: "barbeiros",    rotulo: "Barbeiros" },
    { chave: "horarios",     rotulo: "Horários" }
  ],
  barbeiro: [
    { chave: "agendamentos", rotulo: "Agendamentos" },
    { chave: "contagem",     rotulo: "Contagem" }
  ],
  salao: [
    { chave: "agendamentos", rotulo: "Agendamentos" },
    { chave: "contagem",     rotulo: "Cortes do dia" }
  ]
};

const SECOES = {
  dashboard:    renderDashboard,
  agendamentos: renderAgendamentos,
  contagem:     renderContagem,
  barbeiros:    renderBarbeiros,
  horarios:     renderHorarios
};

// Monta os botões das abas conforme o papel; devolve a chave da 1ª aba.
function montarAbas() {
  const lista = ABAS_POR_PAPEL[API.admin.papel()] || ABAS_POR_PAPEL.barbeiro;
  abas.innerHTML = lista.map((a, i) =>
    `<button class="aba${i === 0 ? " ativa" : ""}" data-secao="${a.chave}">${escapeHTML(a.rotulo)}</button>`
  ).join("");
  return lista[0].chave;
}

function trocarSecao(chave) {
  abas.querySelectorAll(".aba").forEach((a) => {
    a.classList.toggle("ativa", a.dataset.secao === chave);
  });
  const render = SECOES[chave];
  if (render) render();
}

abas.addEventListener("click", (e) => {
  const btn = e.target.closest(".aba");
  if (btn) trocarSecao(btn.dataset.secao);
});

/* =====================================================
   SEÇÃO: DASHBOARD
   Indicadores: agendamentos do dia, agenda semanal,
   faturamento e serviços mais realizados.
   ===================================================== */
let periodoDashboard = "dia";

async function renderDashboard() {
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Dashboard</h2>
    <p class="secao-subtitulo">Visão geral do movimento da barbearia</p>

    <div class="filtros" id="filtros-periodo">
      <span class="secao-subtitulo" style="margin:0 6px 0 0;">Período:</span>
      <button class="chip-filtro" data-periodo="dia">Hoje</button>
      <button class="chip-filtro" data-periodo="semana">Semana</button>
      <button class="chip-filtro" data-periodo="mes">Mês</button>
    </div>

    <div id="dashboard-corpo">${carregando("Carregando indicadores...")}</div>
  `;

  const filtros = document.getElementById("filtros-periodo");
  filtros.querySelectorAll(".chip-filtro").forEach((c) => {
    c.classList.toggle("ativo", c.dataset.periodo === periodoDashboard);
    c.addEventListener("click", () => {
      periodoDashboard = c.dataset.periodo;
      renderDashboard();
    });
  });

  const corpo = document.getElementById("dashboard-corpo");

  try {
    // Relatório do período + agendamentos de hoje (pra lista do dia)
    const [relatorio, agsHoje] = await Promise.all([
      API.admin.relatorio(periodoDashboard),
      API.admin.listarAgendamentos({ data: hojeISO() })
    ]);

    const confirmadosHoje = agsHoje.filter((a) => a.status !== "cancelado");

    const rotuloPeriodo = { dia: "hoje", semana: "na semana", mes: "no mês" }[periodoDashboard];

    corpo.innerHTML = `
      <div class="grid-kpis">
        <div class="kpi">
          <p class="kpi-rotulo">Agendamentos ${escapeHTML(rotuloPeriodo)}</p>
          <p class="kpi-valor">${relatorio.total_agendamentos}</p>
        </div>
        <div class="kpi">
          <p class="kpi-rotulo">Faturamento ${escapeHTML(rotuloPeriodo)}</p>
          <p class="kpi-valor acento">${formatarMoeda(relatorio.faturamento_total)}</p>
        </div>
        <div class="kpi">
          <p class="kpi-rotulo">Confirmados hoje</p>
          <p class="kpi-valor">${confirmadosHoje.length}</p>
        </div>
      </div>

      <div class="bloco">
        <h3 class="bloco-titulo">Serviços mais realizados</h3>
        <div id="ranking-servicos"></div>
      </div>

      <div class="bloco">
        <h3 class="bloco-titulo">Agenda de hoje (${formatarDataBR(hojeISO())})</h3>
        <div id="agenda-hoje"></div>
      </div>
    `;

    renderRanking(document.getElementById("ranking-servicos"), relatorio.servicos_mais_realizados || []);
    renderAgendaHoje(document.getElementById("agenda-hoje"), confirmadosHoje);

  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) corpo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

function renderRanking(container, itens) {
  if (!itens.length) {
    container.innerHTML = vazio("Nenhum serviço realizado no período.");
    return;
  }
  const maxQtd = Math.max(...itens.map((i) => i.quantidade));
  container.innerHTML = itens.map((i) => {
    const largura = maxQtd ? (i.quantidade / maxQtd) * 100 : 0;
    return `
      <div class="ranking-item">
        <span class="ranking-nome">${escapeHTML(i.nome)}</span>
        <span class="ranking-num">${i.quantidade} · ${formatarMoeda(i.faturamento)}</span>
        <div class="ranking-barra-fundo">
          <div class="ranking-barra" style="width:${largura}%"></div>
        </div>
      </div>
    `;
  }).join("");
}

function renderAgendaHoje(container, ags) {
  if (!ags.length) {
    container.innerHTML = vazio("Nenhum agendamento confirmado para hoje.");
    return;
  }
  const ordenados = [...ags].sort((a, b) => a.hora.localeCompare(b.hora));
  container.innerHTML = `
    <div class="tabela-wrap">
      <table class="tabela">
        <thead>
          <tr><th>Hora</th><th>Cliente</th><th>Serviço</th><th>Valor</th></tr>
        </thead>
        <tbody>
          ${ordenados.map((a) => `
            <tr>
              <td data-label="Hora"><strong>${escapeHTML(a.hora)}</strong></td>
              <td data-label="Cliente">${escapeHTML(a.cliente_nome)}</td>
              <td data-label="Serviço">${escapeHTML(a.servico_nome)}</td>
              <td data-label="Valor">${formatarMoeda(a.servico_preco)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

/* =====================================================
   SEÇÃO: CONTAGEM (fechamento do dia + comissão)
   Master vê todos os barbeiros + o total da barbearia.
   Barbeiro vê só a linha dele (o backend já filtra).
   ===================================================== */
let dataContagem = ""; // vazio = hoje

async function renderContagem() {
  const data = dataContagem || hojeISO();
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Contagem</h2>
    <p class="secao-subtitulo">Fechamento do dia</p>

    <div class="form-linha">
      <div class="form-grupo">
        <label class="campo-label" for="contagem-data">Dia</label>
        <input type="date" id="contagem-data" class="campo-input" value="${data}" />
      </div>
    </div>

    <div id="contagem-corpo">${carregando()}</div>
  `;

  const inputData = document.getElementById("contagem-data");
  inputData.addEventListener("change", () => { dataContagem = inputData.value; renderContagem(); });

  const corpo = document.getElementById("contagem-corpo");
  try {
    const r = await API.admin.contagem(data);
    const verValores = r.ver_valores !== false; // salão => false (só quantidade)
    const ehMaster = API.admin.ehMaster();

    if (!r.barbeiros || !r.barbeiros.length) {
      corpo.innerHTML = vazio("Sem dados para esse dia.");
      return;
    }

    if (!verValores) {
      // Salão (tablet): só a quantidade de cortes por barbeiro — sem dinheiro.
      corpo.innerHTML = `
        <div class="tabela-wrap">
          <table class="tabela">
            <thead><tr><th>Barbeiro</th><th>Cortes</th></tr></thead>
            <tbody>
              ${r.barbeiros.map((b) => `
                <tr>
                  <td data-label="Barbeiro"><strong>${escapeHTML(b.barbeiro_nome)}</strong></td>
                  <td data-label="Cortes">${b.clientes}</td>
                </tr>
              `).join("")}
            </tbody>
            <tfoot>
              <tr class="linha-totais">
                <td data-label="Barbeiro"><strong>Total de cortes</strong></td>
                <td data-label="Cortes"><strong>${r.totais.clientes}</strong></td>
              </tr>
            </tfoot>
          </table>
        </div>
      `;
      return;
    }

    // master/barbeiro: tabela completa com valores
    corpo.innerHTML = `
      <div class="tabela-wrap">
        <table class="tabela">
          <thead>
            <tr><th>Barbeiro</th><th>Clientes</th><th>Total</th><th>Comissão</th><th>Barbearia</th></tr>
          </thead>
          <tbody>
            ${r.barbeiros.map((b) => `
              <tr>
                <td data-label="Barbeiro"><strong>${escapeHTML(b.barbeiro_nome)}</strong></td>
                <td data-label="Clientes">${b.clientes}</td>
                <td data-label="Total">${formatarMoeda(b.total)}</td>
                <td data-label="Comissão (${b.comissao_pct}%)">${formatarMoeda(b.barbeiro_recebe)}</td>
                <td data-label="Barbearia">${formatarMoeda(b.barbearia_recebe)}</td>
              </tr>
            `).join("")}
          </tbody>
          ${ehMaster ? `
          <tfoot>
            <tr class="linha-totais">
              <td data-label="Barbeiro"><strong>Totais</strong></td>
              <td data-label="Clientes"><strong>${r.totais.clientes}</strong></td>
              <td data-label="Total"><strong>${formatarMoeda(r.totais.total)}</strong></td>
              <td data-label="Comissão"><strong>${formatarMoeda(r.totais.barbeiro_recebe)}</strong></td>
              <td data-label="Barbearia"><strong>${formatarMoeda(r.totais.barbearia_recebe)}</strong></td>
            </tr>
          </tfoot>` : ""}
        </table>
      </div>
    `;
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) corpo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}


/* =====================================================
   SEÇÃO: AGENDAMENTOS — agenda do dia (timeline)
   Faixas de horário + cards por agendamento. No tablet (tela larga) mostra
   os barbeiros em colunas lado a lado; no celular, um seletor troca a coluna.
   ===================================================== */
let dataAgenda = "";          // vazio = hoje
const ALTURA_HORA = 80;       // px por hora na timeline (dá espaço pros 3 textos do card)

function minutosDe(hhmm) { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; }
function minParaHHMM(min) {
  return String(Math.floor(min / 60)).padStart(2, "0") + ":" + String(min % 60).padStart(2, "0");
}
function addDiasISO(iso, n) {
  const [a, m, d] = iso.split("-").map(Number);
  const dt = new Date(a, m - 1, d);
  dt.setDate(dt.getDate() + n);
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const dd = String(dt.getDate()).padStart(2, "0");
  return `${dt.getFullYear()}-${mm}-${dd}`;
}
function recarregarAgenda() { carregarAgenda(dataAgenda || hojeISO()); }

async function renderAgendamentos() {
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Agendamentos</h2>
    <p class="secao-subtitulo">Consulte, registre e gerencie os agendamentos</p>

    <div class="bloco">
      <h3 class="bloco-titulo">Novo agendamento</h3>
      <div class="form-linha">
        <div class="form-grupo">
          <label class="campo-label" for="ag-barbeiro">Barbeiro</label>
          <select id="ag-barbeiro" class="campo-input"></select>
        </div>
        <div class="form-grupo">
          <label class="campo-label" for="ag-servico">Serviço</label>
          <select id="ag-servico" class="campo-input"></select>
        </div>
        <div class="form-grupo">
          <label class="campo-label" for="ag-data">Data</label>
          <input type="date" id="ag-data" class="campo-input" />
        </div>
        <div class="form-grupo">
          <label class="campo-label" for="ag-hora">Hora</label>
          <input type="time" id="ag-hora" class="campo-input" />
        </div>
      </div>
      <div class="form-linha">
        <div class="form-grupo" style="flex:1;min-width:150px;">
          <label class="campo-label" for="ag-nome">Nome do cliente</label>
          <input type="text" id="ag-nome" class="campo-input" placeholder="Nome" />
        </div>
        <div class="form-grupo" style="flex:1;min-width:140px;">
          <label class="campo-label" for="ag-telefone">Telefone (opcional)</label>
          <input type="tel" id="ag-telefone" class="campo-input" placeholder="(11) 99999-9999" />
        </div>
        <button class="btn-mini" id="btn-add-agendamento">Adicionar</button>
      </div>
      <p class="secao-subtitulo" style="margin:0;">Use para registrar um cliente que chegou sem horário marcado.</p>
      <p class="login-erro" id="ag-erro" style="margin-top:8px;"></p>
    </div>

    <div class="agenda-nav">
      <button class="btn-mini" id="ag-dia-ant" aria-label="Dia anterior">‹</button>
      <input type="date" id="ag-dia" class="campo-input" value="${dataAgenda || hojeISO()}" />
      <button class="btn-mini" id="ag-dia-prox" aria-label="Próximo dia">›</button>
      <button class="btn-mini" id="ag-hoje">Hoje</button>
    </div>

    <div id="agenda-corpo">${carregando()}</div>
  `;

  const inputDia = document.getElementById("ag-dia");
  inputDia.addEventListener("change", () => { dataAgenda = inputDia.value; recarregarAgenda(); });
  document.getElementById("ag-dia-ant").addEventListener("click", () => {
    dataAgenda = addDiasISO(inputDia.value, -1); inputDia.value = dataAgenda; recarregarAgenda();
  });
  document.getElementById("ag-dia-prox").addEventListener("click", () => {
    dataAgenda = addDiasISO(inputDia.value, 1); inputDia.value = dataAgenda; recarregarAgenda();
  });
  document.getElementById("ag-hoje").addEventListener("click", () => {
    dataAgenda = hojeISO(); inputDia.value = dataAgenda; recarregarAgenda();
  });

  configurarFormNovoAgendamento();
  recarregarAgenda();
}

// Popula os selects (barbeiros/serviços) e liga o botão de adicionar.
async function configurarFormNovoAgendamento() {
  const selBarb = document.getElementById("ag-barbeiro");
  const selServ = document.getElementById("ag-servico");
  document.getElementById("ag-data").value = hojeISO(); // pré-seleciona hoje

  try {
    const [barbeiros, servicos] = await Promise.all([API.listarBarbeiros(), API.listarServicos()]);
    selBarb.innerHTML = barbeiros.map((b) => `<option value="${b.id}">${escapeHTML(b.nome)}</option>`).join("");
    selServ.innerHTML = servicos.map((s) => `<option value="${s.id}">${escapeHTML(s.nome)} — ${formatarMoeda(s.preco)}</option>`).join("");
  } catch (erro) {
    document.getElementById("ag-erro").textContent = "Não foi possível carregar barbeiros/serviços.";
  }

  document.getElementById("btn-add-agendamento").addEventListener("click", adicionarAgendamento);
}

async function adicionarAgendamento() {
  const erroEl = document.getElementById("ag-erro");
  const btn = document.getElementById("btn-add-agendamento");
  erroEl.textContent = "";

  const dados = {
    barbeiro_id: document.getElementById("ag-barbeiro").value,
    servico_id: document.getElementById("ag-servico").value,
    data: document.getElementById("ag-data").value,
    hora: document.getElementById("ag-hora").value,
    nome_cliente: document.getElementById("ag-nome").value.trim(),
    telefone: document.getElementById("ag-telefone").value.trim() || null
  };

  if (!dados.nome_cliente) { erroEl.textContent = "Informe o nome do cliente."; return; }
  if (!dados.data || !dados.hora) { erroEl.textContent = "Escolha a data e a hora."; return; }

  btn.disabled = true;
  btn.textContent = "...";
  try {
    await API.admin.criarAgendamento(dados);
    // Limpa os campos do cliente/hora, mantendo barbeiro e data (facilita
    // registrar vários seguidos no mesmo dia).
    document.getElementById("ag-nome").value = "";
    document.getElementById("ag-telefone").value = "";
    document.getElementById("ag-hora").value = "";
    recarregarAgenda();
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) erroEl.textContent = msg;
  } finally {
    btn.disabled = false;
    btn.textContent = "Adicionar";
  }
}

async function carregarAgenda(data) {
  const corpo = document.getElementById("agenda-corpo");
  if (!corpo) return;
  corpo.innerHTML = carregando();

  try {
    // Colunas: master e salão veem todos os barbeiros; barbeiro vê só o dele.
    let colunas;
    if (API.admin.ehMaster() || API.admin.papel() === "salao") {
      const barbs = await API.listarBarbeiros(); // públicos (ativos)
      colunas = barbs.map((b) => ({ id: b.id, nome: b.nome }));
    } else {
      colunas = [{ id: API.admin.barbeiroId(), nome: API.admin.barbeiroNome() || "Você" }];
    }

    const ags = await API.admin.listarAgendamentos({ data });
    renderTimeline(corpo, data, colunas, ags);
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) corpo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

function renderTimeline(container, data, colunas, ags) {
  // Janela do dia (horas): a partir dos agendamentos, com margem 8h–20h.
  let ini = 8 * 60, fim = 20 * 60;
  ags.forEach((a) => {
    const s = minutosDe(a.hora);
    const e = s + (a.servico_duracao || 30);
    if (s < ini) ini = s;
    if (e > fim) fim = e;
  });
  const Hs = Math.floor(ini / 60), He = Math.ceil(fim / 60);
  const altura = (He - Hs) * ALTURA_HORA;
  const verValores = API.admin.podeVerValores();

  // Agrupa por barbeiro
  const porBarbeiro = {};
  colunas.forEach((c) => (porBarbeiro[c.id] = []));
  ags.forEach((a) => { if (porBarbeiro[a.barbeiro_id]) porBarbeiro[a.barbeiro_id].push(a); });

  // Régua de horas
  let regua = "";
  for (let h = Hs; h <= He; h++) {
    regua += `<div class="agenda-hora" style="top:${(h - Hs) * ALTURA_HORA}px">${String(h).padStart(2, "0")}:00</div>`;
  }

  // Linha "agora" (só se for hoje e dentro da janela)
  let agora = "";
  if (data === hojeISO()) {
    const now = new Date();
    const nm = now.getHours() * 60 + now.getMinutes();
    if (nm >= Hs * 60 && nm <= He * 60) {
      agora = `<div class="agenda-agora" style="top:${(nm - Hs * 60) / 60 * ALTURA_HORA}px"></div>`;
    }
  }

  const colunasHTML = colunas.map((c, i) => {
    const cards = (porBarbeiro[c.id] || []).map((a) => {
      const s = minutosDe(a.hora);
      const dur = a.servico_duracao || 30;
      const top = (s - Hs * 60) / 60 * ALTURA_HORA;
      const h = Math.max(dur / 60 * ALTURA_HORA, 50);
      const canc = a.status === "cancelado";
      const valor = (verValores && a.servico_preco != null)
        ? `<span class="agenda-card-valor">${formatarMoeda(a.servico_preco)}</span>` : "";
      return `
        <div class="agenda-card${canc ? " cancelado" : ""}" style="top:${top}px;height:${h}px">
          <div class="agenda-card-topo">
            <span>${escapeHTML(a.hora)}–${minParaHHMM(s + dur)}</span>
            ${canc ? "" : `<button class="agenda-x" data-cancelar="${a.id}" title="Cancelar">×</button>`}
          </div>
          <div class="agenda-card-cliente">${escapeHTML(a.cliente_nome)}</div>
          <div class="agenda-card-servico">${escapeHTML(a.servico_nome)}${valor}</div>
        </div>`;
    }).join("");
    return `
      <div class="agenda-col${i === 0 ? " selecionada" : ""}" data-col="${c.id}">
        <div class="agenda-col-body" style="height:${altura}px">${agora}${cards}</div>
      </div>`;
  }).join("");

  const heads = colunas.map((c, i) =>
    `<div class="agenda-head${i === 0 ? " selecionada" : ""}" data-col="${c.id}">${escapeHTML(c.nome)}</div>`
  ).join("");

  const seletor = colunas.length > 1 ? `
    <div class="agenda-seletor">
      ${colunas.map((c, i) => `<button class="chip-filtro${i === 0 ? " ativo" : ""}" data-selcol="${c.id}">${escapeHTML(c.nome)}</button>`).join("")}
    </div>` : "";

  container.innerHTML = `
    ${seletor}
    <div class="agenda">
      <div class="agenda-heads"><div class="agenda-head-spacer"></div>${heads}</div>
      <div class="agenda-corpo-grid">
        <div class="agenda-gutter" style="height:${altura}px">${regua}</div>
        ${colunasHTML}
      </div>
    </div>
  `;

  // Seletor (celular): troca a coluna visível
  container.querySelectorAll("[data-selcol]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.selcol;
      container.querySelectorAll("[data-selcol]").forEach((c) => c.classList.toggle("ativo", c === btn));
      container.querySelectorAll(".agenda-col, .agenda-head").forEach((el) =>
        el.classList.toggle("selecionada", el.dataset.col === id));
    });
  });

  // Cancelar (no card)
  container.querySelectorAll("[data-cancelar]").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!confirm("Cancelar este agendamento? O horário voltará a ficar livre.")) return;
      try {
        await API.admin.cancelarAgendamento(btn.dataset.cancelar);
        recarregarAgenda();
      } catch (erro) {
        const msg = tratarErro(erro);
        if (msg !== null) alert(msg);
      }
    });
  });
}

/* =====================================================
   SEÇÃO: BARBEIROS
   Inativar (folga/imprevisto) e reativar.
   ===================================================== */
async function renderBarbeiros() {
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Barbeiros</h2>
    <p class="secao-subtitulo">Inative um barbeiro em folga/imprevisto — ele some do site sem perder o histórico</p>
    <div id="lista-barbeiros">${carregando()}</div>
  `;
  carregarListaBarbeiros();
}

async function carregarListaBarbeiros() {
  const lista = document.getElementById("lista-barbeiros");
  if (!lista) return;
  lista.innerHTML = carregando();

  try {
    const barbeiros = await API.admin.listarBarbeiros();
    if (!barbeiros.length) {
      lista.innerHTML = vazio("Nenhum barbeiro cadastrado.");
      return;
    }

    lista.innerHTML = `
      <div class="tabela-wrap">
        <table class="tabela">
          <thead>
            <tr><th>Barbeiro</th><th>Status</th><th>Login</th><th>Comissão</th><th></th></tr>
          </thead>
          <tbody>
            ${barbeiros.map((b) => `
              <tr>
                <td data-label="Barbeiro"><strong>${escapeHTML(b.nome)}</strong></td>
                <td data-label="Status"><span class="badge ${b.ativo ? "ativo" : "inativo"}">${b.ativo ? "Ativo" : "Inativo"}</span></td>
                <td data-label="Login">${b.login_usuario ? escapeHTML(b.login_usuario) : "<span class=\"badge inativo\">sem login</span>"}</td>
                <td data-label="Comissão">${b.comissao_pct}%</td>
                <td class="td-acao">
                  <button class="btn-mini" data-login="${b.id}" data-usuario="${escapeHTML(b.login_usuario || "")}">
                    ${b.login_usuario ? "Editar login" : "Criar login"}
                  </button>
                  <button class="btn-mini ${b.ativo ? "perigo" : ""}"
                          data-barbeiro="${b.id}" data-ativo="${b.ativo}">
                    ${b.ativo ? "Inativar" : "Reativar"}
                  </button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;

    lista.querySelectorAll("[data-barbeiro]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const estaAtivo = btn.dataset.ativo === "1";
        if (estaAtivo && !confirm("Inativar este barbeiro? Ele deixará de aparecer no site para novos agendamentos.")) return;
        btn.disabled = true;
        btn.textContent = "...";
        try {
          await API.admin.definirBarbeiroAtivo(btn.dataset.barbeiro, !estaAtivo);
          carregarListaBarbeiros();
        } catch (erro) {
          const msg = tratarErro(erro);
          if (msg !== null) { btn.disabled = false; alert(msg); carregarListaBarbeiros(); }
        }
      });
    });

    // Criar/editar login do barbeiro (master)
    lista.querySelectorAll("[data-login]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.login;
        const atual = btn.dataset.usuario || "";
        const usuario = prompt("Usuário de login do barbeiro:", atual);
        if (usuario === null || !usuario.trim()) return;
        const senha = prompt(atual
          ? "Nova senha (deixe em branco para manter a atual):"
          : "Senha do barbeiro:");
        if (senha === null) return; // cancelou
        try {
          await API.admin.definirLoginBarbeiro(id, usuario.trim(), senha);
          alert("Login salvo com sucesso.");
          carregarListaBarbeiros();
        } catch (erro) {
          const msg = tratarErro(erro);
          if (msg !== null) alert(msg);
        }
      });
    });

  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) lista.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

/* =====================================================
   SEÇÃO: HORÁRIOS (bloqueios)
   Bloquear dia inteiro ou horário específico + remover.
   ===================================================== */
async function renderHorarios() {
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Horários</h2>
    <p class="secao-subtitulo">Bloqueie um dia inteiro (feriado/folga) ou um horário específico</p>

    <div class="bloco">
      <h3 class="bloco-titulo">Novo bloqueio</h3>
      <div class="form-linha">
        <div class="form-grupo">
          <label class="campo-label" for="bloq-data">Data</label>
          <input type="date" id="bloq-data" class="campo-input" />
        </div>
        <div class="form-grupo">
          <label class="campo-label" for="bloq-hora">Hora (opcional)</label>
          <input type="time" id="bloq-hora" class="campo-input" />
        </div>
        <div class="form-grupo" style="flex:1;min-width:160px;">
          <label class="campo-label" for="bloq-motivo">Motivo (opcional)</label>
          <input type="text" id="bloq-motivo" class="campo-input" placeholder="Feriado, folga..." />
        </div>
        <button class="btn-mini" id="btn-add-bloqueio">Bloquear</button>
      </div>
      <p class="secao-subtitulo" style="margin:0;">Deixe a hora em branco para bloquear o dia inteiro.</p>
      <p class="login-erro" id="bloq-erro" style="margin-top:8px;"></p>
    </div>

    <div id="lista-bloqueios">${carregando()}</div>
  `;

  document.getElementById("bloq-data").min = hojeISO();
  document.getElementById("btn-add-bloqueio").addEventListener("click", adicionarBloqueio);
  carregarListaBloqueios();
}

async function adicionarBloqueio() {
  const data   = document.getElementById("bloq-data").value;
  const hora   = document.getElementById("bloq-hora").value;
  const motivo = document.getElementById("bloq-motivo").value.trim();
  const erroEl = document.getElementById("bloq-erro");
  const btn    = document.getElementById("btn-add-bloqueio");
  erroEl.textContent = "";

  if (!data) { erroEl.textContent = "Escolha uma data."; return; }

  btn.disabled = true;
  btn.textContent = "...";
  try {
    await API.admin.criarBloqueio({ data, hora: hora || null, motivo: motivo || null });
    document.getElementById("bloq-hora").value = "";
    document.getElementById("bloq-motivo").value = "";
    carregarListaBloqueios();
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) erroEl.textContent = msg;
  } finally {
    btn.disabled = false;
    btn.textContent = "Bloquear";
  }
}

async function carregarListaBloqueios() {
  const lista = document.getElementById("lista-bloqueios");
  if (!lista) return;
  lista.innerHTML = carregando();

  try {
    const bloqueios = await API.admin.listarBloqueios();
    if (!bloqueios.length) {
      lista.innerHTML = vazio("Nenhum bloqueio cadastrado.");
      return;
    }

    lista.innerHTML = `
      <div class="tabela-wrap">
        <table class="tabela">
          <thead>
            <tr><th>Data</th><th>Tipo</th><th>Motivo</th><th></th></tr>
          </thead>
          <tbody>
            ${bloqueios.map((b) => `
              <tr>
                <td data-label="Data">${formatarDataBR(b.data)}</td>
                <td data-label="Tipo">${b.hora ? escapeHTML(b.hora) : "<span class=\"badge cancelado\">Dia inteiro</span>"}</td>
                <td data-label="Motivo">${escapeHTML(b.motivo || "—")}</td>
                <td class="td-acao"><button class="btn-mini perigo" data-bloqueio="${b.id}">Remover</button></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;

    lista.querySelectorAll("[data-bloqueio]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "...";
        try {
          await API.admin.removerBloqueio(btn.dataset.bloqueio);
          carregarListaBloqueios();
        } catch (erro) {
          const msg = tratarErro(erro);
          if (msg !== null) { btn.disabled = false; btn.textContent = "Remover"; alert(msg); }
        }
      });
    });

  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) lista.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

/* =====================================================
   BOOT
   ===================================================== */
try {
  if (API.admin.estaLogado()) {
    mostrarPainel();
  } else {
    mostrarLogin();
  }
} catch (erro) {
  console.error("[admin] Falha ao iniciar o painel:", erro);
  mostrarLogin();
}
