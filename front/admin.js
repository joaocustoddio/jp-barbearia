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
    { chave: "agenda_dia", rotulo: "Agenda do dia" },
    { chave: "caderninho", rotulo: "Caderninho" },
    { chave: "almoco",     rotulo: "Almoço" },
    { chave: "contagem",   rotulo: "Contagem" },
    { chave: "expediente", rotulo: "Expediente" },
    { chave: "dashboard",  rotulo: "Dashboard" },
    { chave: "barbeiros",  rotulo: "Barbeiros" },
    { chave: "horarios",   rotulo: "Horários" }
  ],
  barbeiro: [
    { chave: "agenda_dia", rotulo: "Agenda do dia" },
    { chave: "caderninho", rotulo: "Caderninho" },
    { chave: "almoco",     rotulo: "Almoço" },
    { chave: "contagem",   rotulo: "Contagem" }
  ],
  salao: [
    { chave: "agenda_dia", rotulo: "Agenda do dia" },
    { chave: "caderninho", rotulo: "Caderninho" },
    { chave: "contagem",   rotulo: "Cortes do dia" }
  ]
};

const SECOES = {
  agenda_dia: renderAgendaDia,
  caderninho: renderCaderninho,
  almoco:     renderAlmoco,
  contagem:   renderContagem,
  expediente: renderExpediente,
  dashboard:  renderDashboard,
  barbeiros:  renderBarbeiros,
  horarios:   renderHorarios
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
let dataExpediente = ""; // vazio = hoje (aba Expediente)

// Breakdown por serviço, um por linha (ex: "1× Degradê — R$40,00").
// Mostra o valor quando disponível (salão não recebe valor).
function servicosResumo(b) {
  if (!b.servicos || !b.servicos.length) return "";
  return b.servicos.map((s) => {
    const val = (s.valor != null) ? ` — ${formatarMoeda(s.valor)}` : "";
    return `<div>${s.quantidade}× ${escapeHTML(s.nome)}${val}</div>`;
  }).join("");
}

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
                  <td data-label="Barbeiro">
                    <strong>${escapeHTML(b.barbeiro_nome)}</strong>
                    ${servicosResumo(b) ? `<div class="contagem-servicos">${servicosResumo(b)}</div>` : ""}
                  </td>
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
                <td data-label="Barbeiro">
                  <strong>${escapeHTML(b.barbeiro_nome)}</strong>
                  ${servicosResumo(b) ? `<div class="contagem-servicos">${servicosResumo(b)}</div>` : ""}
                  ${b.produtos ? `<div class="contagem-produtos">Produtos: ${formatarMoeda(b.produtos)}${b.produtos_qtd ? ` (${b.produtos_qtd})` : ""}</div>` : ""}
                </td>
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
              <td class="totais-titulo"><strong>JP BARBEARIA</strong></td>
              <td data-label="Clientes"><strong>${r.totais.clientes}</strong></td>
              <td data-label="Total"><strong>${formatarMoeda(r.totais.total)}</strong></td>
              <td data-label="Comissão"><strong>${formatarMoeda(r.totais.barbeiro_recebe)}</strong></td>
              <td data-label="Barbearia"><strong>${formatarMoeda(r.totais.barbearia_recebe)}</strong></td>
            </tr>
          </tfoot>` : ""}
        </table>
      </div>
      ${ehMaster && r.totais.produtos ? `<p class="contagem-caixa">Produtos (caixa, fora da comissão): <strong>${formatarMoeda(r.totais.produtos)}</strong></p>` : ""}
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
let statusAgenda = "ativos";  // "ativos" (confirmados) ou "cancelados" — filtro da timeline
const ALTURA_HORA = 104;      // px por hora na timeline (dá espaço pros textos + marcações de 30min)
const PG_ROTULO = { dinheiro: "Dinheiro", pix: "Pix", cartao: "Cartão" };

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

// ABA 1 — AGENDA DO DIA: só a timeline (visualização), com navegação de dia.
async function renderAgendaDia() {
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Agenda do dia</h2>
    <p class="secao-subtitulo">Veja os agendamentos do dia na linha do tempo</p>

    <div class="bloco">
      <div class="agenda-nav">
        <button class="btn-mini nav-seta" id="ag-dia-ant" aria-label="Dia anterior">‹</button>
        <input type="date" id="ag-dia" class="campo-input" value="${dataAgenda || hojeISO()}" />
        <button class="btn-mini nav-seta" id="ag-dia-prox" aria-label="Próximo dia">›</button>
        <button class="btn-mini nav-btn" id="ag-hoje">Hoje</button>
        <button class="btn-mini nav-btn" id="ag-amanha">Amanhã</button>
      </div>
      <div class="agenda-status">
        <button class="chip-filtro${statusAgenda === "ativos" ? " ativo" : ""}" data-status="ativos">Agendamentos</button>
        <button class="chip-filtro${statusAgenda === "cancelados" ? " ativo" : ""}" data-status="cancelados">Cancelados</button>
      </div>
      <div id="agenda-corpo">${carregando()}</div>
    </div>
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
  document.getElementById("ag-amanha").addEventListener("click", () => {
    dataAgenda = addDiasISO(hojeISO(), 1); inputDia.value = dataAgenda; recarregarAgenda();
  });
  document.querySelectorAll(".agenda-status .chip-filtro").forEach((chip) => {
    chip.addEventListener("click", () => {
      statusAgenda = chip.dataset.status;
      document.querySelectorAll(".agenda-status .chip-filtro").forEach((c) => c.classList.toggle("ativo", c === chip));
      recarregarAgenda();
    });
  });

  recarregarAgenda();
}

// ABA — ALMOÇO: gerenciar o almoço (fixo todo dia OU manual só hoje).
async function renderAlmoco() {
  // Master e salão podem gerenciar o almoço fixo de QUALQUER barbeiro (escolhem
  // no seletor). Barbeiro comum mexe só no próprio.
  const ehGestor = API.admin.ehMaster() || API.admin.papel() === "salao";
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Almoço</h2>
    <p class="secao-subtitulo">Bloqueie seu horário de almoço (60 minutos)</p>

    <div class="bloco">
      <h3 class="bloco-titulo">Almoço fixo (todo dia)</h3>
      <p class="secao-subtitulo" style="margin-top:0;">Vale automaticamente pra todos os dias. Remova pra marcar manual.</p>
      ${ehGestor ? `
      <div class="form-grupo" style="margin-bottom:12px;">
        <label class="campo-label" for="alm-fixo-barbeiro">Barbeiro</label>
        <select id="alm-fixo-barbeiro" class="campo-input"></select>
      </div>` : ""}
      <div class="form-linha" style="align-items:flex-end;">
        <div class="form-grupo">
          <label class="campo-label" for="alm-fixo-hora">Começa às</label>
          <input type="time" id="alm-fixo-hora" class="campo-input" />
        </div>
        <button class="btn-mini" id="alm-fixo-salvar">Salvar fixo</button>
        <button class="btn-mini perigo" id="alm-fixo-remover" hidden>Remover fixo</button>
      </div>
      <p class="secao-subtitulo" id="alm-fixo-status" style="margin:8px 0 0;"></p>
      <p class="login-erro" id="alm-fixo-erro" style="margin-top:4px;"></p>
    </div>

    <div class="bloco">
      <h3 class="bloco-titulo">Almoço só hoje (${formatarDataBR(hojeISO())})</h3>
      <p class="secao-subtitulo" style="margin-top:0;">Bloqueio pontual, só pro dia de hoje.</p>
      <div class="acao-almoco" id="acao-almoco">
        <span class="secao-subtitulo" id="acao-almoco-label" style="margin:0;"></span>
        <button class="btn-mini btn-almoco" id="ag-almoco" hidden></button>
      </div>
    </div>
  `;

  if (ehGestor) {
    try {
      const barbs = await API.listarBarbeiros();
      const sel = document.getElementById("alm-fixo-barbeiro");
      sel.innerHTML = barbs.map((b) => `<option value="${b.id}">${escapeHTML(b.nome)}</option>`).join("");
      sel.addEventListener("change", carregarAlmocoFixo);
    } catch (_) { /* segue sem seletor */ }
  }

  carregarAlmocoFixo();
  document.getElementById("alm-fixo-salvar").addEventListener("click", salvarAlmocoFixo);
  document.getElementById("alm-fixo-remover").addEventListener("click", removerAlmocoFixoUI);
  atualizarBotaoAlmoco(hojeISO());   // almoço manual opera no dia de hoje
}

// Barbeiro alvo do almoço fixo: o do seletor (master/salão) ou o próprio (null =
// o backend usa o barbeiro do token).
function almocoFixoBarbeiro() {
  const sel = document.getElementById("alm-fixo-barbeiro");
  return sel ? Number(sel.value) : null;
}

/* =====================================================
   SEÇÃO: CADERNINHO VIRTUAL
   Marcador principal de encaixe: horário + cliente + telefone
   (opcional) + serviço (com foto, toque pra escolher). Pode
   sobrescrever/encaixar em cima de outro horário. Barbeiro/master
   usa o próprio; salão escolhe o barbeiro no seletor.
   ===================================================== */
async function renderCaderninho() {
  const ehSalao = API.admin.papel() === "salao";
  const hoje = hojeISO();

  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Caderninho virtual</h2>
    <p class="secao-subtitulo">Anote rapidinho um corte de encaixe</p>

    <div class="bloco">
      ${ehSalao ? `
      <div class="form-grupo" style="margin-bottom:12px;">
        <label class="campo-label" for="cad-barbeiro">Barbeiro</label>
        <select id="cad-barbeiro" class="campo-input"></select>
      </div>` : ""}
      <div class="form-linha">
        <div class="form-grupo">
          <label class="campo-label" for="cad-hora">Horário</label>
          <input type="time" id="cad-hora" class="campo-input" />
        </div>
        <div class="form-grupo" style="flex:1;min-width:140px;">
          <label class="campo-label" for="cad-nome">Cliente</label>
          <input type="text" id="cad-nome" class="campo-input" placeholder="Nome" />
        </div>
        <div class="form-grupo" style="flex:1;min-width:130px;">
          <label class="campo-label" for="cad-telefone">Telefone (opcional)</label>
          <input type="tel" id="cad-telefone" class="campo-input" placeholder="(11) 99999-9999" />
        </div>
      </div>
      <label class="campo-label" style="margin-top:6px;">Serviço</label>
      <div class="cad-servicos" id="cad-servicos">${carregando()}</div>
      <p class="login-erro" id="cad-erro" style="margin-top:8px;"></p>
      <button class="btn btn-primario" id="cad-add" style="width:100%;margin-top:8px;">Anotar corte</button>
    </div>

    <div class="bloco">
      <h3 class="bloco-titulo">Cortes de hoje (${formatarDataBR(hoje)})</h3>
      <div id="cad-lista">${carregando()}</div>
    </div>
  `;

  // Salão escolhe o barbeiro; barbeiro/master usa o próprio.
  if (ehSalao) {
    try {
      const barbs = await API.listarBarbeiros();
      const sel = document.getElementById("cad-barbeiro");
      sel.innerHTML = barbs.map((b) => `<option value="${b.id}">${escapeHTML(b.nome)}</option>`).join("");
      sel.addEventListener("change", () => carregarCaderninhoLista(cadBarbeiroAtual(), hoje));
    } catch (_) { /* segue */ }
  }

  // Grid de serviços com foto (toque pra escolher).
  try {
    const servicos = await carregarServicosCache();
    const grid = document.getElementById("cad-servicos");
    grid.innerHTML = servicos.map((s) => `
      <button type="button" class="cad-servico-card" data-servico-id="${s.id}">
        ${s.imagem ? `<img src="img/${s.imagem}" alt="" loading="lazy">` : `<span class="cad-servico-ph"></span>`}
        <span class="cad-servico-nome">${escapeHTML(s.nome)}</span>
        <span class="cad-servico-preco">${formatarMoeda(s.preco)}</span>
      </button>`).join("");
    grid.querySelectorAll(".cad-servico-card").forEach((c) => {
      c.addEventListener("click", () => {
        grid.querySelectorAll(".cad-servico-card").forEach((x) => x.classList.remove("selecionado"));
        c.classList.add("selecionado");
      });
    });
  } catch (_) {
    document.getElementById("cad-servicos").innerHTML = vazio("Não foi possível carregar os serviços.");
  }

  document.getElementById("cad-add").addEventListener("click", anotarCaderninho);
  carregarCaderninhoLista(cadBarbeiroAtual(), hoje);
}

// Barbeiro atual do caderninho: o do seletor (salão) ou o próprio (barbeiro/master).
function cadBarbeiroAtual() {
  const sel = document.getElementById("cad-barbeiro");
  return sel ? Number(sel.value) : API.admin.barbeiroId();
}

async function anotarCaderninho() {
  const erroEl = document.getElementById("cad-erro");
  erroEl.textContent = "";
  const barbeiroId = cadBarbeiroAtual();
  const hora = document.getElementById("cad-hora").value;
  const nome = document.getElementById("cad-nome").value.trim();
  const telefone = document.getElementById("cad-telefone").value.trim();
  const sel = document.querySelector(".cad-servico-card.selecionado");
  if (!barbeiroId) { erroEl.textContent = "Selecione o barbeiro."; return; }
  if (!hora) { erroEl.textContent = "Informe o horário."; return; }
  if (!nome) { erroEl.textContent = "Informe o nome do cliente."; return; }
  if (!sel) { erroEl.textContent = "Escolha o serviço."; return; }

  const btn = document.getElementById("cad-add");
  btn.disabled = true; btn.textContent = "...";
  try {
    await API.admin.criarAgendamento({
      barbeiro_id: barbeiroId, servico_id: sel.dataset.servicoId, data: hojeISO(),
      hora, nome_cliente: nome, telefone: telefone || "", encaixe: true
    });
    document.getElementById("cad-hora").value = "";
    document.getElementById("cad-nome").value = "";
    document.getElementById("cad-telefone").value = "";
    sel.classList.remove("selecionado");
    carregarCaderninhoLista(barbeiroId, hojeISO());
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) erroEl.textContent = msg;
  } finally {
    btn.disabled = false; btn.textContent = "Anotar corte";
  }
}

async function carregarCaderninhoLista(barbeiroId, data) {
  const alvo = document.getElementById("cad-lista");
  if (!alvo) return;
  alvo.innerHTML = carregando();
  try {
    let ags = await API.admin.listarAgendamentos({ data });
    ags = ags.filter((a) => a.status !== "cancelado" && Number(a.barbeiro_id) === Number(barbeiroId));
    ags.sort((a, b) => a.hora.localeCompare(b.hora));
    if (!ags.length) { alvo.innerHTML = vazio("Nenhum corte anotado hoje."); return; }
    alvo.innerHTML = `
      <table class="tabela">
        <thead><tr><th>Hora</th><th>Cliente</th><th>Serviço</th><th></th></tr></thead>
        <tbody>
          ${ags.map((a) => `
            <tr>
              <td data-rotulo="Hora">${escapeHTML(a.hora.slice(0, 5))}${a.encaixe ? ' <span class="tag-encaixe">encaixe</span>' : ''}</td>
              <td data-rotulo="Cliente">${escapeHTML(a.cliente_nome)}</td>
              <td data-rotulo="Serviço">${escapeHTML(a.servico_nome)}</td>
              <td data-rotulo=""><button class="btn-mini perigo" data-rem-cad="${a.id}">Remover</button></td>
            </tr>`).join("")}
        </tbody>
      </table>
    `;
    alvo.querySelectorAll("[data-rem-cad]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Remover este corte?")) return;
        try {
          await API.admin.cancelarAgendamento(btn.dataset.remCad);
          carregarCaderninhoLista(barbeiroId, data);
        } catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
      });
    });
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) alvo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

// Almoço fixo (todo dia): carrega o estado atual e ajusta o input/botão.
async function carregarAlmocoFixo() {
  const status = document.getElementById("alm-fixo-status");
  const btnRem = document.getElementById("alm-fixo-remover");
  const input = document.getElementById("alm-fixo-hora");
  if (!status) return;
  try {
    const r = await API.admin.obterAlmocoFixo(almocoFixoBarbeiro());
    if (r && r.almoco_fixo) {
      input.value = r.almoco_fixo.slice(0, 5);
      status.textContent = `Almoço fixo ativo às ${r.almoco_fixo.slice(0, 5)} (todo dia).`;
      btnRem.hidden = false;
    } else {
      status.textContent = "Nenhum almoço fixo — você marca manual quando quiser.";
      btnRem.hidden = true;
    }
  } catch (e) { const m = tratarErro(e); if (m !== null) status.textContent = m; }
}

async function salvarAlmocoFixo() {
  const erroEl = document.getElementById("alm-fixo-erro");
  erroEl.textContent = "";
  const hora = document.getElementById("alm-fixo-hora").value;
  if (!hora) { erroEl.textContent = "Escolha o horário do almoço."; return; }
  const btn = document.getElementById("alm-fixo-salvar");
  btn.disabled = true;
  try {
    await API.admin.definirAlmocoFixo(hora, almocoFixoBarbeiro());
    carregarAlmocoFixo();
  } catch (e) { const m = tratarErro(e); if (m !== null) erroEl.textContent = m; }
  finally { btn.disabled = false; }
}

async function removerAlmocoFixoUI() {
  if (!confirm("Remover o almoço fixo? Você volta a marcar manualmente por dia.")) return;
  try { await API.admin.removerAlmocoFixo(almocoFixoBarbeiro()); carregarAlmocoFixo(); }
  catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
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
    // Filtra pela aba escolhida: "ativos" (não cancelados) ou "cancelados".
    // Separa os dois pra não sobrescreverem no mesmo horário (fica ilegível).
    const filtrados = ags.filter((a) =>
      statusAgenda === "cancelados" ? a.status === "cancelado" : a.status !== "cancelado"
    );
    // Almoços aparecem na timeline como bloco (cor diferente) — só na aba de ativos.
    let almocos = [];
    if (statusAgenda !== "cancelados") {
      try { almocos = await API.admin.listarAlmocos(data); } catch (_) { /* segue sem almoços */ }
    }
    renderTimeline(corpo, data, colunas, filtrados, almocos);
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) corpo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

// Botão de almoço: só pro barbeiro (cada um gerencia o próprio). Mostra
// "Marcar almoço" quando não há; "Almoço HH:MM — liberar" quando já marcou.
async function atualizarBotaoAlmoco(data) {
  const btn = document.getElementById("ag-almoco");
  const wrap = document.getElementById("acao-almoco");
  const label = document.getElementById("acao-almoco-label");
  if (!btn) return;
  // Barbeiro e master (JP também corta) gerenciam o próprio almoço; salão não.
  const papel = API.admin.papel();
  if (papel !== "barbeiro" && papel !== "master") {
    if (wrap) wrap.hidden = true;
    btn.hidden = true;
    return;
  }
  if (wrap) wrap.hidden = false;
  if (label) label.textContent = `Almoço de ${formatarDataBR(data)}:`;
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
      try { await API.admin.liberarAlmoco(data); atualizarBotaoAlmoco(data); }
      catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
    };
  } else {
    btn.classList.remove("marcado");
    btn.textContent = "Marcar almoço";
    btn.onclick = async () => {
      const hora = prompt("A que horas começa o almoço? (formato HH:MM, ex: 12:30)\nFica bloqueado por 60 minutos.");
      if (!hora) return;
      if (!/^\d{2}:\d{2}$/.test(hora.trim())) { alert("Hora inválida. Use o formato HH:MM (ex: 12:30)."); return; }
      try { await API.admin.marcarAlmoco(data, hora.trim()); atualizarBotaoAlmoco(data); }
      catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
    };
  }
}

// Empilha cards em ordem cronológica. Quando dois se sobrepõem no tempo,
// empurra o segundo para baixo para não cobrir o anterior.
function empilharCards(cards) {
  const ordenados = cards.slice().sort((a, b) => a.s - b.s || a.e - b.e);
  return ordenados;
}

function renderTimeline(container, data, colunas, ags, almocos) {
  almocos = almocos || [];
  // Janela do dia (horas): a partir dos agendamentos + almoços, com margem 8h–20h.
  let ini = 8 * 60, fim = 20 * 60;
  ags.forEach((a) => {
    const s = minutosDe(a.hora);
    const e = s + (a.servico_duracao || 30);
    if (s < ini) ini = s;
    if (e > fim) fim = e;
  });
  almocos.forEach((al) => {
    const s = minutosDe(al.hora);
    const e = s + (al.duracao_min || 60);
    if (s < ini) ini = s;
    if (e > fim) fim = e;
  });
  const Hs = Math.floor(ini / 60), He = Math.ceil(fim / 60);
  const altura = (He - Hs) * ALTURA_HORA;
  const verValores = API.admin.podeVerValores();

  // Agrupa por barbeiro (agendamentos e almoços)
  const porBarbeiro = {};
  const porAlmoco = {};
  colunas.forEach((c) => { porBarbeiro[c.id] = []; porAlmoco[c.id] = []; });
  ags.forEach((a) => { if (porBarbeiro[a.barbeiro_id]) porBarbeiro[a.barbeiro_id].push(a); });
  almocos.forEach((al) => { if (porAlmoco[al.barbeiro_id]) porAlmoco[al.barbeiro_id].push(al); });

  // Régua de horas — marcações de 30 em 30 min (a "meia" fica mais discreta).
  let regua = "";
  for (let m = Hs * 60; m <= He * 60; m += 30) {
    const top = (m - Hs * 60) / 60 * ALTURA_HORA;
    const meia = m % 60 !== 0;
    regua += `<div class="agenda-hora${meia ? " meia" : ""}" style="top:${top}px">${minParaHHMM(m)}</div>`;
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
    const lista = (porBarbeiro[c.id] || []).map((a) => {
      const s = minutosDe(a.hora);
      const dur = a.servico_duracao || 30;
      return { tipo: "ag", a, s, e: s + dur, dur };
    });
    (porAlmoco[c.id] || []).forEach((al) => {
      const s = minutosDe(al.hora);
      const dur = al.duracao_min || 60;
      lista.push({ tipo: "almoco", al, s, e: s + dur, dur });
    });
    let prevBottom = -Infinity;
    const GAP = 6;
    const cards = empilharCards(lista).map((p) => {
      const s = p.s;
      const dur = p.dur;
      let top = (s - Hs * 60) / 60 * ALTURA_HORA;
      const h = Math.max(dur / 60 * ALTURA_HORA, 50) - 2;
      if (top < prevBottom + GAP) top = prevBottom + GAP;
      prevBottom = top + h;
      const pos = `top:${top}px;height:${h}px;left:3px;right:3px;`;

      // Card de ALMOÇO: cor diferente, sem ações. Só pra o barbeiro visualizar.
      if (p.tipo === "almoco") {
        const al = p.al;
        return `
          <div class="agenda-card agenda-almoco" style="${pos}">
            <div class="agenda-card-topo"><span>${escapeHTML(al.hora)}–${minParaHHMM(s + dur)}</span></div>
            <div class="agenda-card-cliente">Almoço</div>
          </div>`;
      }

      const a = p.a;
      const canc = a.status === "cancelado";
      const valor = (verValores && a.servico_preco != null)
        ? `<span class="agenda-card-valor">${formatarMoeda(a.servico_preco)}</span>` : "";
      const wpp = linkWhatsApp(a);
      const btnWpp = (!canc && wpp)
        ? `<a class="agenda-wpp" href="${wpp}" target="_blank" rel="noopener" title="Enviar lembrete no WhatsApp" onclick="event.stopPropagation()">
             <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.82 11.82 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884a9.86 9.86 0 001.51 5.26l-.999 3.648 3.489-.919zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>
           </a>`
        : "";
      const btnMenu = canc ? "" : `<button class="agenda-menu" data-menu="${a.id}" title="Ações" onclick="event.stopPropagation()">⋮</button>`;
      const acoes = canc ? "" : `<span class="agenda-acoes">${btnWpp}${btnMenu}</span>`;
      const pgBadge = (verValores && a.forma_pagamento)
        ? `<span class="pg-badge">${PG_ROTULO[a.forma_pagamento] || ""}</span>` : "";
      const prodCent = a.consumos_total_centavos || 0;
      const prodBadge = (verValores && prodCent)
        ? `<span class="prod-badge">+${formatarMoeda(prodCent / 100)}</span>` : "";
      return `
        <div class="agenda-card${canc ? " cancelado" : ""}" style="${pos}">
          <div class="agenda-card-topo">
            <span>${escapeHTML(a.hora)}–${minParaHHMM(s + dur)}</span>
            ${acoes}
          </div>
          <div class="agenda-card-cliente">${escapeHTML(a.cliente_nome)}${a.encaixe ? ' <span class="tag-encaixe">encaixe</span>' : ''}</div>
          <div class="agenda-card-servico">${escapeHTML(a.servico_nome)}${valor}${pgBadge}${prodBadge}</div>
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

  // Menu de ações do card: WhatsApp fica direto no card; o ⋮ abre o resto
  // (informações, forma de pagamento, produtos, remarcar, cancelar).
  const porId = {};
  ags.forEach((a) => { porId[a.id] = a; });
  container.querySelectorAll("[data-menu]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const a = porId[btn.dataset.menu];
      if (a) abrirMenuCard(ev, a, data);
    });
  });
}

/* -------------------- Menu de ações do card (popover) -------------------- */
function fecharMenuCard() {
  const m = document.querySelector(".card-menu");
  if (m) m.remove();
}

function abrirMenuCard(ev, a, dataRef) {
  fecharMenuCard();
  const verVal = API.admin.podeVerValores();
  const dur = a.servico_duracao || 30;
  const fim = minParaHHMM(minutosDe(a.hora) + dur);
  const wpp = linkWhatsApp(a);
  const prodTotal = (a.consumos_total_centavos || 0) / 100;
  const infoVal = (verVal && a.servico_preco != null) ? ` · ${formatarMoeda(a.servico_preco)}` : "";
  const tel = a.cliente_telefone ? `<div class="card-menu-info">${escapeHTML(a.cliente_telefone)}</div>` : "";

  const pop = document.createElement("div");
  pop.className = "card-menu";
  pop.innerHTML = `
    <div class="card-menu-nome">${escapeHTML(a.cliente_nome)}${a.encaixe ? ' <span class="tag-encaixe">encaixe</span>' : ''}</div>
    <div class="card-menu-info">${escapeHTML(a.servico_nome)} · ${escapeHTML(a.hora)}–${fim} (${dur}min)${infoVal}</div>
    ${tel}
    ${verVal ? `
    <div class="card-menu-sec">Pagamento</div>
    <div class="card-menu-pgto">
      ${["dinheiro", "pix", "cartao"].map((f) =>
        `<button class="pg-chip${a.forma_pagamento === f ? " ativo" : ""}" data-pg="${f}">${PG_ROTULO[f]}</button>`).join("")}
    </div>
    <button class="card-menu-item" data-consumo>Produtos${prodTotal ? ` — ${formatarMoeda(prodTotal)}` : ""}</button>
    ` : ""}
    <div class="card-menu-acoes">
      ${wpp ? `<a class="card-menu-item" href="${wpp}" target="_blank" rel="noopener">WhatsApp</a>` : ""}
      <button class="card-menu-item" data-rep>Remarcar em 7 dias</button>
      <button class="card-menu-item perigo" data-canc>Cancelar agendamento</button>
    </div>`;
  document.body.appendChild(pop);

  // Posiciona perto do clique, sem sair da tela.
  const x = Math.min(ev.clientX, window.innerWidth - pop.offsetWidth - 8);
  const y = Math.min(ev.clientY, window.innerHeight - pop.offsetHeight - 8);
  pop.style.left = Math.max(8, x) + "px";
  pop.style.top = Math.max(8, y) + "px";

  pop.querySelectorAll("[data-pg]").forEach((b) => {
    b.addEventListener("click", async () => {
      const forma = a.forma_pagamento === b.dataset.pg ? "" : b.dataset.pg;  // clicar de novo desmarca
      try {
        await API.admin.registrarPagamento(a.id, forma);
        a.forma_pagamento = forma || null;
        pop.querySelectorAll("[data-pg]").forEach((x) => x.classList.toggle("ativo", x.dataset.pg === forma));
      } catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
    });
  });
  const btnCons = pop.querySelector("[data-consumo]");
  if (btnCons) btnCons.addEventListener("click", () => { fecharMenuCard(); abrirModalConsumo(a); });
  pop.querySelector("[data-rep]").addEventListener("click", () => {
    fecharMenuCard();
    abrirModalRepetir(a, addDiasISO(dataRef || hojeISO(), 7));
  });
  pop.querySelector("[data-canc]").addEventListener("click", async () => {
    fecharMenuCard();
    if (!confirm("Cancelar este agendamento? O horário voltará a ficar livre.")) return;
    try { await API.admin.cancelarAgendamento(a.id); recarregarAgenda(); }
    catch (e) { const m = tratarErro(e); if (m !== null) alert(m); }
  });

  // Fecha ao clicar fora (no próximo clique).
  setTimeout(() => document.addEventListener("click", fecharMenuCard, { once: true }), 0);
}

// Modal de produtos (consumo) de um atendimento: lista + adicionar + remover.
// Cache do catálogo de adicionais (não muda no meio da sessão).
let _produtosCache = null;
async function carregarProdutosCache() {
  if (!_produtosCache) _produtosCache = await API.admin.listarProdutos();
  return _produtosCache;
}

// Modal de adicionais: catálogo com − / + por item, total ao vivo, salva o conjunto.
async function abrirModalConsumo(a) {
  let produtos = [];
  const atuais = {};   // produto_id -> quantidade já lançada
  try {
    produtos = await carregarProdutosCache();
    const cons = await API.admin.listarConsumos(a.id);
    cons.forEach((c) => { if (c.produto_id) atuais[c.produto_id] = c.quantidade; });
  } catch (e) { const m = tratarErro(e); if (m !== null) { alert(m); return; } }

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box" role="dialog" aria-modal="true">
      <h3 class="modal-titulo">Adicionais — ${escapeHTML(a.cliente_nome)}</h3>
      <div class="prod-catalogo">
        ${produtos.map((p) => `
          <div class="prod-row" data-pid="${p.id}" data-preco="${p.preco_centavos}">
            <div class="prod-info">
              <span class="prod-nome">${escapeHTML(p.nome)}</span>
              <span class="prod-preco">${formatarMoeda(p.preco_centavos / 100)}</span>
            </div>
            <div class="prod-stepper">
              <button class="prod-menos" type="button" aria-label="menos">−</button>
              <span class="prod-qtd">${atuais[p.id] || 0}</span>
              <button class="prod-mais" type="button" aria-label="mais">+</button>
            </div>
          </div>`).join("")}
      </div>
      <div class="prod-total">Total: <strong id="prod-total-val">R$ 0,00</strong></div>
      <p class="login-erro" id="consumo-erro"></p>
      <div class="modal-acoes">
        <button class="btn-mini" id="consumo-fechar">Fechar</button>
        <button class="btn btn-primario" id="consumo-salvar">Salvar</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const fechar = () => overlay.remove();
  overlay.addEventListener("click", (e) => { if (e.target === overlay) fechar(); });
  overlay.querySelector("#consumo-fechar").addEventListener("click", fechar);

  const recalc = () => {
    let tot = 0;
    overlay.querySelectorAll(".prod-row").forEach((row) => {
      const q = parseInt(row.querySelector(".prod-qtd").textContent, 10) || 0;
      tot += q * parseInt(row.dataset.preco, 10);
    });
    overlay.querySelector("#prod-total-val").textContent = formatarMoeda(tot / 100);
  };
  recalc();

  overlay.querySelectorAll(".prod-row").forEach((row) => {
    const qEl = row.querySelector(".prod-qtd");
    const set = (n) => { qEl.textContent = Math.max(0, n); recalc(); };
    row.querySelector(".prod-mais").addEventListener("click", () => set((parseInt(qEl.textContent, 10) || 0) + 1));
    row.querySelector(".prod-menos").addEventListener("click", () => set((parseInt(qEl.textContent, 10) || 0) - 1));
  });

  overlay.querySelector("#consumo-salvar").addEventListener("click", async () => {
    const itens = [];
    overlay.querySelectorAll(".prod-row").forEach((row) => {
      const q = parseInt(row.querySelector(".prod-qtd").textContent, 10) || 0;
      if (q > 0) itens.push({ produto_id: Number(row.dataset.pid), quantidade: q });
    });
    const btn = overlay.querySelector("#consumo-salvar");
    btn.disabled = true;
    try {
      await API.admin.salvarConsumos(a.id, itens);
      fechar();
      recarregarAgenda();   // atualiza o badge do card
    } catch (e) {
      const m = tratarErro(e);
      if (m !== null) { overlay.querySelector("#consumo-erro").textContent = m; btn.disabled = false; }
    }
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
/* =====================================================
   SEÇÃO: EXPEDIENTE (só master)
   Ajusta o horário (início/fim) de cada barbeiro num dia específico.
   Afeta os horários que o cliente vê pra marcar naquele barbeiro/dia.
   ===================================================== */
async function renderExpediente() {
  const data = dataExpediente || hojeISO();
  elConteudo.innerHTML = `
    <h2 class="secao-titulo">Expediente</h2>
    <p class="secao-subtitulo">Ajuste o horário de cada barbeiro no dia (quem chega mais tarde ou sai mais cedo)</p>

    <div class="bloco">
      <div class="agenda-nav">
        <button class="btn-mini nav-seta" id="exp-ant" aria-label="Dia anterior">‹</button>
        <input type="date" id="exp-data" class="campo-input" value="${data}" />
        <button class="btn-mini nav-seta" id="exp-prox" aria-label="Próximo dia">›</button>
        <button class="btn-mini nav-btn" id="exp-hoje">Hoje</button>
        <button class="btn-mini nav-btn" id="exp-amanha">Amanhã</button>
      </div>
      <div id="exp-corpo">${carregando()}</div>
    </div>
  `;
  const input = document.getElementById("exp-data");
  input.addEventListener("change", () => { dataExpediente = input.value; renderExpediente(); });
  document.getElementById("exp-ant").addEventListener("click", () => { dataExpediente = addDiasISO(input.value, -1); renderExpediente(); });
  document.getElementById("exp-prox").addEventListener("click", () => { dataExpediente = addDiasISO(input.value, 1); renderExpediente(); });
  document.getElementById("exp-hoje").addEventListener("click", () => { dataExpediente = hojeISO(); renderExpediente(); });
  document.getElementById("exp-amanha").addEventListener("click", () => { dataExpediente = addDiasISO(hojeISO(), 1); renderExpediente(); });

  carregarExpedientes(data);
}

async function carregarExpedientes(data) {
  const corpo = document.getElementById("exp-corpo");
  if (!corpo) return;
  corpo.innerHTML = carregando();
  try {
    const r = await API.admin.listarExpedientes(data);
    if (r.fechado) { corpo.innerHTML = vazio("A barbearia não abre nesse dia."); return; }
    if (!r.barbeiros || !r.barbeiros.length) { corpo.innerHTML = vazio("Nenhum barbeiro ativo."); return; }
    corpo.innerHTML = `
      <p class="secao-subtitulo" style="margin:0 0 14px;">Horário padrão do dia: <strong>${r.padrao.inicio}–${r.padrao.fim}</strong></p>
      <div class="exp-lista">
        ${r.barbeiros.map((b) => `
          <div class="exp-item" data-barb="${b.barbeiro_id}">
            <div class="exp-nome">
              ${escapeHTML(b.barbeiro_nome)}
              ${b.personalizado ? '<span class="tag-encaixe">ajustado</span>' : ''}
            </div>
            <div class="exp-horas">
              <input type="time" class="campo-input exp-ini" value="${b.inicio}" />
              <span>até</span>
              <input type="time" class="campo-input exp-fim" value="${b.fim}" />
            </div>
            <div class="exp-acoes">
              <button class="btn-mini exp-salvar">Salvar</button>
              <button class="btn-mini exp-normal"${b.personalizado ? "" : " hidden"}>Normal</button>
            </div>
            <p class="login-erro exp-erro" style="margin:6px 0 0;"></p>
          </div>`).join("")}
      </div>
    `;
    corpo.querySelectorAll(".exp-item").forEach((item) => {
      const barb = item.dataset.barb;
      const erroEl = item.querySelector(".exp-erro");
      item.querySelector(".exp-salvar").addEventListener("click", async () => {
        erroEl.textContent = "";
        const ini = item.querySelector(".exp-ini").value;
        const fim = item.querySelector(".exp-fim").value;
        if (!ini || !fim) { erroEl.textContent = "Preencha início e fim."; return; }
        try { await API.admin.definirExpediente(barb, data, ini, fim); carregarExpedientes(data); }
        catch (e) { const m = tratarErro(e); if (m !== null) erroEl.textContent = m; }
      });
      item.querySelector(".exp-normal").addEventListener("click", async () => {
        erroEl.textContent = "";
        try { await API.admin.removerExpediente(barb, data); carregarExpedientes(data); }
        catch (e) { const m = tratarErro(e); if (m !== null) erroEl.textContent = m; }
      });
    });
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) corpo.innerHTML = `<div class="painel-erro">${escapeHTML(msg)}</div>`;
  }
}

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

// Vincula o Telegram do barbeiro: ele passa a receber SÓ os agendamentos dele,
// em vez de todo mundo receber tudo no mesmo grupo.
async function abrirModalTelegram(barbeiroId, nome, chatAtual) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box" role="dialog" aria-modal="true">
      <h3 class="modal-titulo">Avisos de ${escapeHTML(nome)}</h3>
      <p class="modal-cliente">Com um canal próprio, ${escapeHTML(nome)} recebe só os
      agendamentos dele. Sem canal, tudo cai no grupo geral.</p>

      <label class="campo-label">Conversas que o bot enxerga</label>
      <div id="tg-conversas" class="tg-conversas">${carregando()}</div>

      <label class="campo-label" for="tg-chat">ID do canal</label>
      <input type="text" id="tg-chat" class="campo-input" placeholder="-1001234567890"
             value="${escapeHTML(chatAtual || "")}" />
      <p class="secao-subtitulo" style="margin:0;font-size:12px;">
        O barbeiro precisa mandar uma mensagem pro bot antes de aparecer na lista.
      </p>
      <p class="login-erro" id="tg-erro"></p>
      <div class="modal-acoes">
        <button class="btn-mini" id="tg-fechar">Cancelar</button>
        <button class="btn-mini perigo" id="tg-limpar">Usar grupo geral</button>
        <button class="btn btn-primario" id="tg-salvar">Salvar</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const fechar = () => overlay.remove();
  overlay.addEventListener("click", (e) => { if (e.target === overlay) fechar(); });
  overlay.querySelector("#tg-fechar").addEventListener("click", fechar);
  const inputChat = overlay.querySelector("#tg-chat");

  // Lista as conversas pra escolher clicando (evita caçar o ID na mão).
  try {
    const conversas = await API.admin.listarConversasTelegram();
    const box = overlay.querySelector("#tg-conversas");
    box.innerHTML = conversas.length
      ? conversas.map((c) => `
          <button type="button" class="tg-item" data-id="${escapeHTML(String(c.id))}">
            <span>${escapeHTML(c.nome)}</span>
            <span class="tg-tipo">${escapeHTML(c.tipo || "")} · ${escapeHTML(String(c.id))}</span>
          </button>`).join("")
      : `<p class="secao-subtitulo" style="margin:0;">Nenhuma conversa recente. Peça pro
         barbeiro mandar uma mensagem pro bot e reabra esta tela.</p>`;
    box.querySelectorAll(".tg-item").forEach((item) => {
      item.addEventListener("click", () => {
        inputChat.value = item.dataset.id;
        box.querySelectorAll(".tg-item").forEach((x) => x.classList.toggle("selecionado", x === item));
      });
    });
  } catch (erro) {
    const msg = tratarErro(erro);
    if (msg !== null) overlay.querySelector("#tg-conversas").innerHTML =
      `<p class="secao-subtitulo" style="margin:0;">${escapeHTML(msg)}</p>`;
  }

  const salvar = async (valor) => {
    const erroEl = overlay.querySelector("#tg-erro");
    erroEl.textContent = "";
    try {
      await API.admin.definirTelegramBarbeiro(barbeiroId, valor);
      fechar();
      carregarListaBarbeiros();
    } catch (e) { const m = tratarErro(e); if (m !== null) erroEl.textContent = m; }
  };
  overlay.querySelector("#tg-salvar").addEventListener("click", () => salvar(inputChat.value.trim()));
  overlay.querySelector("#tg-limpar").addEventListener("click", () => salvar(""));
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
            <tr><th>Barbeiro</th><th>Status</th><th>Login</th><th>Comissão</th><th>Avisos</th><th></th></tr>
          </thead>
          <tbody>
            ${barbeiros.map((b) => `
              <tr>
                <td data-label="Barbeiro"><strong>${escapeHTML(b.nome)}</strong></td>
                <td data-label="Status"><span class="badge ${b.ativo ? "ativo" : "inativo"}">${b.ativo ? "Ativo" : "Inativo"}</span></td>
                <td data-label="Login">${b.login_usuario ? escapeHTML(b.login_usuario) : "<span class=\"badge inativo\">sem login</span>"}</td>
                <td data-label="Comissão">${b.comissao_pct}%</td>
                <td data-label="Avisos">${b.telegram_chat_id
                    ? '<span class="badge ativo">canal próprio</span>'
                    : '<span class="badge inativo">grupo geral</span>'}</td>
                <td class="td-acao">
                  <button class="btn-mini" data-telegram="${b.id}" data-chat="${escapeHTML(b.telegram_chat_id || "")}" data-nome="${escapeHTML(b.nome)}">
                    Avisos no Telegram
                  </button>
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

    lista.querySelectorAll("[data-telegram]").forEach((btn) => {
      btn.addEventListener("click", () => abrirModalTelegram(
        btn.dataset.telegram, btn.dataset.nome, btn.dataset.chat));
    });

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
