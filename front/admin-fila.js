/* =====================================================
   admin-fila.js
   Fila online de fim de ano no painel.
   - renderDiasDeFila: cartão do dono em Expediente, onde ele cadastra os dias
     em que o site não marca horário e o atendimento é por ordem de chegada.
   Carregado ANTES do admin.js (que usa as funções daqui).
   ===================================================== */

function dataCurtaBR(iso) {
  const [, m, d] = String(iso).split("-");
  return `${d}/${m}`;
}

async function renderDiasDeFila(container) {
  container.innerHTML = `
    <div class="bloco dias-fila">
      <h3 class="secao-titulo" style="font-size:1.05rem;margin:0 0 4px;">Dias de fila</h3>
      <p class="secao-subtitulo" style="margin:0 0 12px;">Nesses dias o site não marca horário: o cliente entra na fila por ordem de chegada.</p>
      <div class="dias-fila-lista"></div>
      <div class="dias-fila-form" style="display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin-top:12px;">
        <label style="display:flex;flex-direction:column;gap:4px;">De
          <input type="date" id="fila-inicio" class="campo-input" />
        </label>
        <label style="display:flex;flex-direction:column;gap:4px;">Até
          <input type="date" id="fila-fim" class="campo-input" />
        </label>
        <button class="btn-mini nav-btn" id="fila-salvar">Salvar dias de fila</button>
      </div>
      <p class="dias-fila-erro" role="alert" style="margin:8px 0 0;color:var(--erro,#ef5350);"></p>
    </div>`;

  const lista = container.querySelector(".dias-fila-lista");
  const erro = container.querySelector(".dias-fila-erro");

  async function carregar() {
    try {
      const periodos = await API.admin.periodosFila.listar();
      if (!periodos.length) {
        lista.innerHTML = `<p class="secao-subtitulo" style="margin:0;">Nenhum dia de fila cadastrado.</p>`;
        return;
      }
      lista.innerHTML = periodos.map((p) => `
        <div class="dias-fila-item" data-id="${p.id}" style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;">
          <span>${dataCurtaBR(p.inicio)} a ${dataCurtaBR(p.fim)}</span>
          <button class="btn-mini nav-btn dias-fila-apagar">Apagar</button>
        </div>`).join("");
    } catch (e) {
      lista.innerHTML = "";
      erro.textContent = e.message || "Não foi possível carregar os dias de fila.";
    }
  }

  // Apagar pede confirmação na própria página: o painel não usa confirm().
  lista.addEventListener("click", async (ev) => {
    const botao = ev.target.closest(".dias-fila-apagar");
    if (!botao) return;
    if (botao.dataset.confirmar !== "1") {
      botao.dataset.confirmar = "1";
      botao.textContent = "Confirmar";
      return;
    }
    botao.disabled = true;
    erro.textContent = "";
    try {
      await API.admin.periodosFila.apagar(botao.closest(".dias-fila-item").dataset.id);
      carregar();
    } catch (e) {
      botao.disabled = false;
      erro.textContent = e.message || "Não foi possível apagar.";
    }
  });

  container.querySelector("#fila-salvar").addEventListener("click", async (ev) => {
    const inicio = container.querySelector("#fila-inicio").value;
    const fim = container.querySelector("#fila-fim").value;
    erro.textContent = "";
    if (!inicio || !fim) { erro.textContent = "Escolha as datas de início e fim."; return; }
    if (fim < inicio) { erro.textContent = "A data final vem antes da inicial."; return; }
    ev.target.disabled = true;
    try {
      await API.admin.periodosFila.criar(inicio, fim);
      container.querySelector("#fila-inicio").value = "";
      container.querySelector("#fila-fim").value = "";
      carregar();
    } catch (e) {
      erro.textContent = e.message || "Não foi possível salvar.";
    } finally {
      ev.target.disabled = false;
    }
  });

  carregar();
}


/* =====================================================
   ABA "FILA" — a fila de hoje no painel.
   Um bloco por barbeiro com "Chamar próximo", quem foi chamado (com a
   tolerância) e quem está na cadeira; depois a lista de quem aguarda e os
   finalizados. Atualiza a cada 10 s enquanto a aba estiver aberta.
   ===================================================== */

const FILA_ATUALIZA_MS = 10000;
let filaTimer = null;
let filaUltima = null;   // última listagem (barbeiros pro formulário)

const ROTULO_STATUS = {
  concluido: "Concluído", nao_veio: "Não veio", saiu: "Saiu", removido: "Removido",
};

function minutosDesde(iso) {
  return iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 60000) : 0;
}

function nomeDoBarbeiro(r, id) {
  if (id == null) return "Qualquer um";
  const nome = (r.nomes || {})[String(id)];
  if (nome) return nome;
  const b = (r.barbeiros || []).find((x) => x.id === id);
  return b ? b.nome : "Outro barbeiro";
}

function trabalhandoHoje(r) {
  return (r.barbeiros || []).map((b) => b.id);
}

// Botões que mandam o cliente pra um barbeiro. Dono e salão escolhem quem atende
// (é assim que se redistribui a fila de um barbeiro que ficou indisponível); o
// barbeiro, logado, atende ele mesmo. Nunca oferece um barbeiro fora do dia.
function acoesComBarbeiro(l, r, acoes, preferido) {
  const ids = trabalhandoHoje(r);
  if (!ids.length) return "";
  const padrao = ids.includes(preferido) ? preferido : ids[0];
  if (API.admin.papel() === "barbeiro") {
    return acoes.map(([acao, rotulo]) =>
      `<button class="btn-mini nav-btn" data-acao="${acao}" data-id="${l.id}" data-barbeiro="${padrao}">${rotulo}</button>`).join("");
  }
  const opcoes = ids.map((id) =>
    `<option value="${id}"${id === padrao ? " selected" : ""}>${escapeHTML(nomeDoBarbeiro(r, id))}</option>`).join("");
  return `<select class="campo-input pf-quem" aria-label="Quem atende">${opcoes}</select>`
    + acoes.map(([acao, rotulo]) =>
      `<button class="btn-mini nav-btn" data-acao="${acao}" data-id="${l.id}" data-escolhe="1">${rotulo}</button>`).join("");
}

function cartaoChamado(l, r) {
  const resta = r.tolerancia_min - minutosDesde(l.chamado_em);
  return `
    <div class="pf-card pf-chamado">
      <div class="pf-card-info"><b>${escapeHTML(l.nome)}</b><span>${escapeHTML(l.servico || "")} · chamado</span>
        ${resta > 0
          ? `<span class="pf-tolerancia">Tolerância: ${resta} min</span>`
          : `<span class="pf-atrasado">Passou da tolerância</span>`}
      </div>
      <div class="pf-acoes">
        ${l.whatsapp ? `<a class="btn-mini nav-btn pf-wa" href="${escapeHTML(l.whatsapp)}" target="_blank" rel="noopener">WhatsApp</a>` : ""}
        ${trabalhandoHoje(r).includes(l.atendido_por)
          ? `<button class="btn-mini nav-btn pf-principal" data-acao="comecar" data-id="${l.id}">Começar</button>`
          : acoesComBarbeiro(l, r, [["comecar", "Começar"]], null)}
        <button class="btn-mini nav-btn" data-acao="nao-veio" data-id="${l.id}">Não veio</button>
      </div>
    </div>`;
}

function cartaoAtendendo(l) {
  return `
    <div class="pf-card">
      <div class="pf-card-info"><b>${escapeHTML(l.nome)}</b><span>${escapeHTML(l.servico || "")} · na cadeira</span></div>
      <div class="pf-acoes"><button class="btn-mini nav-btn pf-principal" data-acao="concluir" data-id="${l.id}">Concluir</button></div>
    </div>`;
}

function cartaoAguardando(l, r) {
  const indisponivel = (r.indisponiveis || []).includes(l.barbeiro_id);
  return `
    <div class="pf-card">
      <div class="pf-card-info"><b>${escapeHTML(l.nome)}</b>
        <span>${escapeHTML(l.servico || "")} · ${escapeHTML(nomeDoBarbeiro(r, l.barbeiro_id))} · há ${minutosDesde(l.entrou_em)} min</span>
        ${indisponivel ? `<span class="pf-atrasado">Barbeiro indisponível hoje</span>`
          : (l.inicio_estimado ? `<span>Previsto ${escapeHTML(l.inicio_estimado)}</span>` : "")}
      </div>
      <div class="pf-acoes">
        ${acoesComBarbeiro(l, r, [["chamar", "Chamar"], ["comecar", "Começar"]], l.barbeiro_id)}
        <button class="btn-mini nav-btn" data-acao="remover" data-id="${l.id}">Remover</button>
      </div>
    </div>`;
}

function desenharFila(r) {
  const corpo = document.getElementById("pf-corpo");
  if (!corpo) return;
  const lugares = r.lugares || [];
  // Um bloco por barbeiro trabalhando, mais um pra quem ficou indisponível com
  // alguém chamado ou na cadeira — senão esse cliente sumiria do painel.
  const ids = trabalhandoHoje(r);
  const ausentes = [...new Set(lugares
    .filter((l) => ["chamado", "atendendo"].includes(l.status) && l.atendido_por != null
                   && !ids.includes(l.atendido_por))
    .map((l) => l.atendido_por))];
  const blocos = [...ids, ...ausentes].map((id) => {
    const presente = ids.includes(id);
    const chamado = lugares.find((l) => l.status === "chamado" && l.atendido_por === id);
    const cadeira = lugares.filter((l) => l.status === "atendendo" && l.atendido_por === id);
    return `
      <section class="fila-bloco" data-bloco="${id}">
        <div class="pf-bloco-topo"><h3>${escapeHTML(nomeDoBarbeiro(r, id))}${presente ? "" : ` <span class="pf-atrasado">(indisponível hoje)</span>`}</h3>
          ${presente ? `<button class="btn pf-chamar" data-chamar="${id}">Chamar próximo</button>` : ""}</div>
        ${chamado ? cartaoChamado(chamado, r) : ""}
        ${cadeira.map(cartaoAtendendo).join("")}
      </section>`;
  }).join("");
  const aguardando = lugares.filter((l) => l.status === "aguardando");
  const finalizados = lugares.filter((l) => ROTULO_STATUS[l.status]);
  corpo.innerHTML = `
    ${r.dia_de_fila ? "" : `<p class="secao-subtitulo">Hoje não é dia de fila.</p>`}
    ${blocos || vazio("Nenhum barbeiro trabalhando hoje.")}
    <h3 class="pf-subtitulo">Aguardando (${aguardando.length})</h3>
    ${aguardando.length ? aguardando.map((l) => cartaoAguardando(l, r)).join("") : `<p class="secao-subtitulo">Ninguém esperando.</p>`}
    ${finalizados.length ? `
      <details class="pf-finalizados" open><summary>Finalizados hoje (${finalizados.length})</summary>
        ${finalizados.map((l) => `<div class="pf-final"><span>${escapeHTML(l.nome)}</span><span>${ROTULO_STATUS[l.status]}</span></div>`).join("")}
      </details>` : ""}`;
}

async function atualizarFila() {
  const erro = document.querySelector("#painel-fila .pf-erro");
  try {
    filaUltima = await API.admin.fila.listar();
    if (erro) erro.textContent = "";
    desenharFila(filaUltima);
  } catch (e) {
    if (erro) erro.textContent = e.message || "Não foi possível carregar a fila.";
  }
}

async function abrirFormularioFila(form) {
  form.hidden = !form.hidden;
  if (form.hidden) return;
  const servicos = await API.listarServicos(hojeISO()).catch(() => []);
  const barbeiros = (filaUltima && filaUltima.barbeiros) || [];
  form.innerHTML = `
    <label>Nome<input id="pf-nome" class="campo-input" required></label>
    <label>WhatsApp (opcional)<input id="pf-tel" class="campo-input" type="tel" inputmode="numeric"></label>
    <label>Serviço<select id="pf-servico" class="campo-input">
      ${servicos.map((s) => `<option value="${s.id}">${escapeHTML(s.nome)}</option>`).join("")}</select></label>
    <label>Barbeiro<select id="pf-barbeiro" class="campo-input">
      <option value="">Qualquer um</option>
      ${barbeiros.map((b) => `<option value="${b.id}">${escapeHTML(b.nome)}</option>`).join("")}</select></label>
    <button class="btn" type="submit">Pôr na fila</button>`;
}

async function renderFila() {
  elConteudo.innerHTML = `
    <div id="painel-fila" class="painel-fila">
      <div class="pf-topo">
        <h2 class="secao-titulo">Fila de hoje</h2>
        <button class="btn-mini nav-btn" id="pf-abrir">+ Pôr na fila</button>
      </div>
      <form id="pf-form" class="pf-form" hidden></form>
      <p class="pf-erro" role="alert"></p>
      <div id="pf-corpo">${carregando()}</div>
    </div>`;
  const painel = document.getElementById("painel-fila");
  const form = document.getElementById("pf-form");
  const erro = painel.querySelector(".pf-erro");

  document.getElementById("pf-abrir").addEventListener("click", () => abrirFormularioFila(form));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const escolha = document.getElementById("pf-barbeiro").value;
    const dados = {
      nome: document.getElementById("pf-nome").value.trim(),
      servico_id: Number(document.getElementById("pf-servico").value),
      barbeiro_id: escolha ? Number(escolha) : null,
    };
    const tel = document.getElementById("pf-tel").value.trim();
    if (tel) dados.telefone = tel;
    try {
      await API.admin.fila.porNaFila(dados);
      form.hidden = true;
      atualizarFila();
    } catch (e) { erro.textContent = e.message; }
  });

  painel.addEventListener("click", async (ev) => {
    const chamar = ev.target.closest("[data-chamar]");
    const acao = ev.target.closest("[data-acao]");
    if (!chamar && !acao) return;
    const botao = chamar || acao;
    // Remover e Não veio pedem um segundo toque, no próprio botão.
    if (acao && ["remover", "nao-veio"].includes(acao.dataset.acao) && acao.dataset.confirmar !== "1") {
      acao.dataset.confirmar = "1";
      acao.textContent = "Confirmar";
      return;
    }
    botao.disabled = true;
    erro.textContent = "";
    try {
      if (chamar) await API.admin.fila.chamarProximo(Number(chamar.dataset.chamar));
      else {
        const seletor = acao.dataset.escolhe && acao.closest(".pf-card").querySelector(".pf-quem");
        const quem = acao.dataset.barbeiro || (seletor && seletor.value);
        await API.admin.fila.acao(acao.dataset.id, acao.dataset.acao, quem ? Number(quem) : null);
      }
      await atualizarFila();
    } catch (e) {
      erro.textContent = e.message;
      botao.disabled = false;
    }
  });

  await atualizarFila();
  clearInterval(filaTimer);
  filaTimer = setInterval(() => {
    if (!document.getElementById("painel-fila")) { clearInterval(filaTimer); return; }
    if (!document.hidden) atualizarFila();
  }, FILA_ATUALIZA_MS);
}

/** Nos dias de fila a aba Fila vai pra frente e abre primeiro. */
function promoverAbaFila() {
  API.fila.hoje().then((r) => {
    if (!r || !r.dia_de_fila) return;
    const linha = document.querySelector(".abas-linha");
    const aba = linha && linha.querySelector('[data-secao="fila"]');
    if (!aba) return;
    linha.prepend(aba);
    trocarSecao("fila");
  }).catch(() => {});
}
