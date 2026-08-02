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
  // "Trocar minha senha": só pra quem tem conta individual (master/barbeiro).
  // O salão é tablet compartilhado — a senha dele fica com o master.
  btnMinhaSenha.hidden = API.admin.papel() === "salao";

  // Mostra QUEM está logado, pra não confundir de login (ex: "Rian").
  const elUsuario = document.getElementById("usuario-atual");
  const papel = API.admin.papel();
  const rotulo = papel === "salao" ? "Salão" : (API.admin.barbeiroNome() || "");
  if (rotulo) { elUsuario.textContent = rotulo; elUsuario.hidden = false; }
  else { elUsuario.hidden = true; }

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

// Trocar a PRÓPRIA senha (self-service). Salão é compartilhado, então não mostra.
const btnMinhaSenha = document.getElementById("btn-minha-senha");
btnMinhaSenha.addEventListener("click", async () => {
  const atual = prompt("Sua senha atual:");
  if (!atual) return;
  const nova = prompt("Nova senha (mínimo 4 caracteres):");
  if (!nova) return;
  if (nova.trim().length < 4) { alert("A nova senha deve ter pelo menos 4 caracteres."); return; }
  try {
    await API.admin.trocarMinhaSenha(atual, nova.trim());
    alert("Senha alterada com sucesso.");
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) alert(msg);
  }
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

/* Monta o link wa.me com a mensagem de lembrete já preenchida.
   Retorna "" se não tiver telefone. Número: só dígitos, com 55 (BR) na frente. */
function linkWhatsApp(a) {
  let tel = (a.cliente_telefone || "").replace(/\D/g, "");
  if (!tel) return "";
  if (tel.length <= 11) tel = "55" + tel;   // adiciona DDI do Brasil se não veio
  const barbeiro = a.barbeiro_nome || "nosso profissional";
  const msg =
    "*JP BARBEARIA*\n" +
    `Olá, passando pra lembrar do serviço agendado com o profissional ${barbeiro} às ${a.hora}. ` +
    "Caso de desistência nos comunique com antecedência.";
  return `https://wa.me/${tel}?text=${encodeURIComponent(msg)}`;
}

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
      <button class="btn-mini btn-almoco" id="ag-almoco" hidden></button>
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
    atualizarBotaoAlmoco(data);
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) corpo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

// Botão de almoço: só pro barbeiro (cada um gerencia o próprio). Mostra
// "Marcar almoço" quando não há; "Almoço HH:MM — liberar" quando já marcou.
async function atualizarBotaoAlmoco(data) {
  const btn = document.getElementById("ag-almoco");
  if (!btn) return;
  if (API.admin.papel() !== "barbeiro") { btn.hidden = true; return; }
  btn.hidden = false;
  btn.disabled = true;
  btn.textContent = "Almoço";
  let almoco = null;
  try { almoco = await API.admin.obterAlmoco(data); } catch (_) { /* silencioso */ }
  btn.disabled = false;

  if (almoco && almoco.hora) {
    btn.classList.add("marcado");
    btn.textContent = `Almoço ${almoco.hora.slice(0, 5)} — liberar`;
    btn.onclick = async () => {
      if (!confirm(`Liberar o almoço das ${almoco.hora.slice(0, 5)}? Os horários voltam a ficar livres.`)) return;
      try { await API.admin.liberarAlmoco(data); recarregarAgenda(); }
      catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
    };
  } else {
    btn.classList.remove("marcado");
    btn.textContent = "Marcar almoço";
    btn.onclick = async () => {
      const hora = prompt("A que horas começa o almoço? (formato HH:MM, ex: 12:30)\nFica bloqueado por 60 minutos.");
      if (!hora) return;
      if (!/^\d{2}:\d{2}$/.test(hora.trim())) { alert("Hora inválida. Use o formato HH:MM (ex: 12:30)."); return; }
      try { await API.admin.marcarAlmoco(data, hora.trim()); recarregarAgenda(); }
      catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
    };
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
      const wpp = linkWhatsApp(a);
      const btnWpp = (!canc && wpp)
        ? `<a class="agenda-wpp" href="${wpp}" target="_blank" rel="noopener" title="Enviar lembrete no WhatsApp" onclick="event.stopPropagation()">
             <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.82 11.82 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884a9.86 9.86 0 001.51 5.26l-.999 3.648 3.489-.919zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>
           </a>`
        : "";
      const btnRep = canc ? "" : `<button class="agenda-rep" data-repetir="${a.id}" title="Repetir este agendamento daqui a 7 dias">
             <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 014-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 01-4 4H3"/></svg>
           </button>`;
      const acoes = canc ? "" : `<span class="agenda-acoes">${btnWpp}${btnRep}<button class="agenda-x" data-cancelar="${a.id}" title="Cancelar">×</button></span>`;
      return `
        <div class="agenda-card${canc ? " cancelado" : ""}" style="top:${top}px;height:${h}px">
          <div class="agenda-card-topo">
            <span>${escapeHTML(a.hora)}–${minParaHHMM(s + dur)}</span>
            ${acoes}
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

  // Repetir (no card): recria o mesmo agendamento 7 dias depois.
  const porId = {};
  ags.forEach((a) => { porId[a.id] = a; });
  container.querySelectorAll("[data-repetir]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const a = porId[btn.dataset.repetir];
      if (a) abrirModalRepetir(a, addDiasISO(data, 7));
    });
  });
}

// Cache dos serviços (pro select do modal de repetir) — evita refetch a cada clique.
let _servicosCache = null;
async function carregarServicosCache() {
  if (!_servicosCache) _servicosCache = await API.listarServicos();
  return _servicosCache;
}

// Abre uma telinha pra repetir o agendamento podendo ajustar dia, hora e serviço
// antes de confirmar (padrão: mesmo serviço, +7 dias, mesma hora).
async function abrirModalRepetir(a, dataPadrao) {
  let servicos = [];
  try { servicos = await carregarServicosCache(); } catch (_) { /* segue sem lista */ }
  const opts = servicos.map((s) =>
    `<option value="${s.id}"${Number(s.id) === Number(a.servico_id) ? " selected" : ""}>${escapeHTML(s.nome)} — ${formatarMoeda(s.preco)}</option>`
  ).join("");

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box" role="dialog" aria-modal="true">
      <h3 class="modal-titulo">Repetir agendamento</h3>
      <p class="modal-cliente">Cliente: <strong>${escapeHTML(a.cliente_nome)}</strong></p>
      <label class="campo-label" for="rep-servico">Serviço</label>
      <select id="rep-servico" class="campo-input">${opts}</select>
      <div class="modal-linha">
        <div class="modal-campo">
          <label class="campo-label" for="rep-data">Data</label>
          <input type="date" id="rep-data" class="campo-input" value="${dataPadrao}" />
        </div>
        <div class="modal-campo">
          <label class="campo-label" for="rep-hora">Hora</label>
          <input type="time" id="rep-hora" class="campo-input" value="${escapeHTML(a.hora.slice(0, 5))}" />
        </div>
      </div>
      <p class="login-erro" id="rep-erro"></p>
      <div class="modal-acoes">
        <button class="btn-mini" id="rep-cancelar">Cancelar</button>
        <button class="btn btn-primario" id="rep-confirmar">Confirmar</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const fechar = () => overlay.remove();
  overlay.addEventListener("click", (e) => { if (e.target === overlay) fechar(); });
  overlay.querySelector("#rep-cancelar").addEventListener("click", fechar);

  overlay.querySelector("#rep-confirmar").addEventListener("click", async () => {
    const servico_id = overlay.querySelector("#rep-servico").value;
    const dataNova = overlay.querySelector("#rep-data").value;
    const horaNova = overlay.querySelector("#rep-hora").value;
    const erroEl = overlay.querySelector("#rep-erro");
    erroEl.textContent = "";
    if (!dataNova || !horaNova) { erroEl.textContent = "Escolha a data e a hora."; return; }

    const btn = overlay.querySelector("#rep-confirmar");
    btn.disabled = true; btn.textContent = "...";
    try {
      await API.admin.criarAgendamento({
        nome_cliente: a.cliente_nome,
        telefone: a.cliente_telefone || "",
        servico_id,
        barbeiro_id: a.barbeiro_id,
        data: dataNova,
        hora: horaNova
      });
      fechar();
      // Navega pro dia do novo agendamento pra dar feedback visual de que caiu.
      dataAgenda = dataNova;
      const inputDia = document.getElementById("ag-dia");
      if (inputDia) inputDia.value = dataNova;
      recarregarAgenda();
    } catch (erro) {
      const msg = tratarErro(erro);
      if (msg !== null) { erroEl.textContent = msg; btn.disabled = false; btn.textContent = "Confirmar"; }
    }
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

    <div class="bloco" style="margin-top:20px;">
      <h3 class="bloco-titulo">Acessos e senhas</h3>
      <p class="secao-subtitulo" style="margin-top:0;">Troque a senha de qualquer login do painel (master, salão e barbeiros).</p>
      <div id="lista-acessos">${carregando()}</div>
    </div>
  `;
  carregarListaBarbeiros();
  carregarAcessos();
}

// Lista os logins do painel com botão de trocar senha (master-only).
async function carregarAcessos() {
  const alvo = document.getElementById("lista-acessos");
  if (!alvo) return;
  alvo.innerHTML = carregando();
  const ROTULO_PAPEL = { master: "Dono (master)", salao: "Salão (tablet)", barbeiro: "Barbeiro" };
  try {
    const acessos = await API.admin.listarAcessos();
    if (!acessos.length) { alvo.innerHTML = vazio("Nenhum login cadastrado."); return; }
    alvo.innerHTML = `
      <table class="tabela">
        <thead><tr><th>Usuário</th><th>Tipo</th><th>Barbeiro</th><th>Senha</th></tr></thead>
        <tbody>
          ${acessos.map((a) => `
            <tr>
              <td data-rotulo="Usuário">${escapeHTML(a.usuario)}</td>
              <td data-rotulo="Tipo">${escapeHTML(ROTULO_PAPEL[a.papel] || a.papel)}</td>
              <td data-rotulo="Barbeiro">${escapeHTML(a.barbeiro_nome || "—")}</td>
              <td data-rotulo="Senha"><button class="btn-mini" data-senha="${escapeHTML(a.usuario)}">Trocar senha</button></td>
            </tr>`).join("")}
        </tbody>
      </table>
    `;
    alvo.querySelectorAll("[data-senha]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const usuario = btn.dataset.senha;
        const nova = prompt(`Nova senha para "${usuario}" (mínimo 4 caracteres):`);
        if (!nova) return;
        if (nova.trim().length < 4) { alert("A senha deve ter pelo menos 4 caracteres."); return; }
        btn.disabled = true;
        try {
          await API.admin.trocarSenha(usuario, nova.trim());
          alert(`Senha de "${usuario}" atualizada.`);
        } catch (erro) {
          const msg = tratarErro(erro);
          if (msg !== null) alert(msg);
        } finally {
          btn.disabled = false;
        }
      });
    });
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) alvo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
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
