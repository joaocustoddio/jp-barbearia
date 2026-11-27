/* =====================================================
   fila.js — fila online de fim de ano (fila.html)

   O cliente escolhe o barbeiro (ou "qualquer um"), entra na fila e acompanha
   a vez nesta página. O código do lugar (token) fica salvo no aparelho só
   pelo dia. A página consulta a situação a cada 15 s enquanto está visível
   e avisa com som e vibração quando falta pouco e quando é chamado.
   ===================================================== */

const CHAVE_TOKEN = "fila_token";
const INTERVALO_CONSULTA_MS = 15000;
const app = document.getElementById("fila-app");
const avisoReconectando = document.getElementById("fila-reconectando");

let consulta = null;            // setInterval da situação
let avisouPerto = false;        // vibração do "Já vem vindo" sai uma vez só
let somLigado = false;          // o navegador só libera som depois de um toque
let audio = null;
let bipe = null;                // setInterval do som do "É sua vez"
let parouSom = false;           // o cliente tocou em "Estou indo"
let escolhido = null;           // barbeiro escolhido no cartão (null = qualquer um)
let filaHoje = null;            // última resposta de /api/fila/hoje

/* ---------------- utilidades ---------------- */

function esc(txt) {
  const d = document.createElement("div");
  d.textContent = txt == null ? "" : String(txt);
  return d.innerHTML;
}

function hojeLocal() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// O lugar fica no endereço da página (fila.html?t=...) e no aparelho. O
// endereço é o "link próprio": abre o lugar em outro navegador — quem entrou
// pelo navegador do Instagram e depois abriu no Chrome não perde a vez.
function lerToken() {
  const doLink = new URLSearchParams(location.search).get("t");
  if (doLink) { guardarToken(doLink); return doLink; }
  try {
    const salvo = JSON.parse(localStorage.getItem(CHAVE_TOKEN));
    if (salvo && salvo.token && salvo.data === hojeLocal()) { mostrarNoEndereco(salvo.token); return salvo.token; }
  } catch (_) { /* sem armazenamento: segue sem lugar salvo */ }
  esquecerToken();
  return null;
}

function mostrarNoEndereco(token) {
  try { history.replaceState(null, "", token ? `fila.html?t=${encodeURIComponent(token)}` : "fila.html"); } catch (_) {}
}

function guardarToken(token) {
  try { localStorage.setItem(CHAVE_TOKEN, JSON.stringify({ token, data: hojeLocal() })); } catch (_) {}
  mostrarNoEndereco(token);
}

function esquecerToken() {
  try { localStorage.removeItem(CHAVE_TOKEN); } catch (_) {}
  if (new URLSearchParams(location.search).has("t")) mostrarNoEndereco(null);
}

function dataCurta(iso) {
  const [, m, d] = String(iso).split("-");
  return `${d}/${m}`;
}

function textoEspera(min) {
  if (min == null) return "calculando a espera";
  return min === 0 ? "é agora" : `uns ${min} min`;
}

function vibrar(padrao) {
  if (navigator.vibrate) navigator.vibrate(padrao);
}

/* ---------------- som ---------------- */

function ligarSom() {
  if (somLigado) return;
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
    somLigado = true;
  } catch (_) { /* navegador sem áudio: fica só o aviso na tela */ }
}

function tocarBipe() {
  if (!audio || !somLigado) return;
  const osc = audio.createOscillator();
  const vol = audio.createGain();
  osc.frequency.value = 880;
  vol.gain.setValueAtTime(0.25, audio.currentTime);
  vol.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.6);
  osc.connect(vol).connect(audio.destination);
  osc.start();
  osc.stop(audio.currentTime + 0.6);
}

function pararBipe() {
  if (bipe) clearInterval(bipe);
  bipe = null;
}

/* ---------------- tela: escolher e entrar ---------------- */

async function mostrarEscolha() {
  pararConsulta();
  pararBipe();
  app.innerHTML = `<p class="fila-texto" style="margin-top:22px;">Carregando a fila…</p>`;
  let hoje, servicos;
  try {
    [hoje, servicos] = await Promise.all([API.fila.hoje(), API.listarServicos(hojeLocal())]);
  } catch (e) {
    app.innerHTML = `<p class="fila-olho">Fila de chegada</p>
      <p class="fila-erro">${esc(e.message)}</p>
      <button class="fila-btn secundario" id="fila-tentar">Tentar de novo</button>`;
    document.getElementById("fila-tentar").addEventListener("click", mostrarEscolha);
    return;
  }

  if (!hoje || !hoje.dia_de_fila) {
    const proximo = hoje && hoje.proximo_dia_de_fila ? dataCurta(hoje.proximo_dia_de_fila) : null;
    let texto = "Hoje o atendimento é com horário marcado. A fila online funciona nos dias de fim de ano.";
    let agendar = true;
    if (hoje && hoje.fechado_hoje) {
      texto = proximo ? `Hoje a barbearia está fechada. A fila volta em ${proximo}.` : "Hoje a barbearia está fechada.";
      agendar = false;   // nos dias de fila o site não marca horário
    } else if (proximo) {
      texto = `A fila online começa em ${proximo}. Até lá, marque seu horário pelo site.`;
    }
    app.innerHTML = `
      <p class="fila-olho">Fila de chegada</p>
      <h1 class="fila-titulo">Fila agora</h1>
      <p class="fila-texto">${esc(texto)}</p>
      ${agendar ? `<a class="fila-btn" style="display:block;text-align:center;text-decoration:none;" href="index.html">Agendar horário</a>` : ""}`;
    return;
  }
  filaHoje = hoje;

  const qualquer = hoje.qualquer_um || {};
  const cartoes = (hoje.barbeiros || []).map((b) => `
    <button type="button" class="cartao-barbeiro" data-id="${b.id}" ${b.aberta ? "" : "disabled"}
            aria-pressed="false">
      <span class="cartao-avatar">${b.foto ? `<img src="img/${esc(b.foto)}" alt="">` : esc(b.nome.charAt(0))}</span>
      <span class="cartao-info"><b>${esc(b.nome)}</b>
        <span>${b.aberta ? `${b.esperando} esperando · ${textoEspera(b.espera_min)}` : esc(b.motivo || "Fila fechada")}</span>
      </span>
    </button>`).join("");

  app.innerHTML = `
    <p class="fila-olho">Fila de chegada</p>
    <h1 class="fila-titulo">Fila agora</h1>
    <p class="fila-texto">Atendimento por ordem de chegada. Entre na fila pelo celular e chegue perto da sua vez.</p>
    ${hoje.abre_as ? `<p class="fila-aviso">A fila abre às ${esc(hoje.abre_as)}.</p>` : ""}
    <div class="fila-cartoes" role="group" aria-label="Escolha o barbeiro">
      <button type="button" class="cartao-barbeiro" data-id="" ${qualquer.aberta ? "" : "disabled"} aria-pressed="false">
        <span class="cartao-avatar">★</span>
        <span class="cartao-info"><b>Qualquer um</b>
          <span>${qualquer.aberta ? `O primeiro que liberar · ${textoEspera(qualquer.espera_min)}` : esc(qualquer.motivo || "Fila fechada")}</span>
        </span>
      </button>
      ${cartoes}
    </div>
    <form class="fila-form" id="fila-form" novalidate>
      <label class="fila-campo">Nome
        <input id="fila-nome" autocomplete="given-name" placeholder="Como te chamamos" required />
      </label>
      <label class="fila-campo">WhatsApp
        <input id="fila-telefone" type="tel" inputmode="numeric" autocomplete="tel" placeholder="11999998888" required />
      </label>
      <label class="fila-campo">Serviço
        <select id="fila-servico">
          ${(servicos || []).map((s) => `<option value="${s.id}">${esc(s.nome)} · R$ ${Number(s.preco).toFixed(2).replace(".", ",")}</option>`).join("")}
        </select>
      </label>
      <p class="fila-erro" id="fila-erro" role="alert"></p>
      <button class="fila-btn" type="submit">Entrar na fila</button>
    </form>`;

  const botoes = Array.from(app.querySelectorAll(".cartao-barbeiro"));
  function marcar(botao) {
    botoes.forEach((b) => b.setAttribute("aria-pressed", String(b === botao)));
    escolhido = botao.dataset.id ? Number(botao.dataset.id) : null;
  }
  botoes.forEach((b) => b.addEventListener("click", () => marcar(b)));
  const primeiroAberto = botoes.find((b) => !b.disabled);
  if (primeiroAberto) marcar(primeiroAberto);

  document.getElementById("fila-form").addEventListener("submit", entrar);
}

async function entrar(ev) {
  ev.preventDefault();
  ligarSom();   // este toque libera o som do aviso
  const erro = document.getElementById("fila-erro");
  const botao = ev.target.querySelector("button[type=submit]");
  const nome = document.getElementById("fila-nome").value.trim();
  const telefone = document.getElementById("fila-telefone").value.trim();
  erro.textContent = "";
  if (!app.querySelector('.cartao-barbeiro[aria-pressed="true"]')) {
    const naoAbriu = filaHoje && filaHoje.qualquer_um && filaHoje.qualquer_um.motivo === "Ainda não abriu";
    erro.textContent = naoAbriu ? "A fila de hoje ainda não abriu." : "A fila de hoje já fechou.";
    return;
  }
  if (nome.length < 2) { erro.textContent = "Diga seu nome."; return; }
  if (telefone.replace(/\D/g, "").length < 10) { erro.textContent = "Informe o WhatsApp com DDD."; return; }
  botao.disabled = true;
  try {
    const r = await API.fila.entrar({
      nome, telefone,
      servico_id: Number(document.getElementById("fila-servico").value),
      barbeiro_id: escolhido,
    });
    guardarToken(r.token);
    acompanhar(r.token);
  } catch (e) {
    erro.textContent = e.message;
    botao.disabled = false;
  }
}

/* ---------------- tela: na fila ---------------- */

function pararConsulta() {
  if (consulta) clearInterval(consulta);
  consulta = null;
}

function acompanhar(token) {
  pararConsulta();
  avisouPerto = false;
  parouSom = false;
  const atualizar = () => consultar(token);
  atualizar();
  consulta = setInterval(() => { if (!document.hidden) atualizar(); }, INTERVALO_CONSULTA_MS);
}

async function consultar(token) {
  let s;
  try {
    s = await API.fila.situacao(token);
  } catch (e) {
    if (e.status === 404) {
      esquecerToken();
      mostrarFim("Esse link não vale mais", "Esse lugar na fila não existe mais. Se quiser, entre de novo.");
      return;
    }
    avisoReconectando.hidden = false;
    return;
  }
  avisoReconectando.hidden = true;
  mostrarSituacao(token, s);
}

function horaLimite(chamadoEm, tolerancia) {
  const limite = new Date(new Date(chamadoEm).getTime() + tolerancia * 60000);
  return `${String(limite.getHours()).padStart(2, "0")}:${String(limite.getMinutes()).padStart(2, "0")}`;
}

function mostrarSituacao(token, s) {
  if (s.status === "chamado") {
    if (!app.querySelector(".fila-chamado")) {
      app.innerHTML = `
        <div class="fila-chamado" role="alert">
          <h2>É sua vez!</h2>
          <p>Você tem até ${horaLimite(s.chamado_em, s.tolerancia_min)} para chegar.<br>${esc(s.barbeiro)} · ${esc(s.servico)}</p>
          <button class="fila-btn" id="fila-indo">Estou indo</button>
        </div>`;
      document.getElementById("fila-indo").addEventListener("click", () => {
        parouSom = true;
        pararBipe();
        document.getElementById("fila-indo").textContent = "Te esperamos!";
      });
      vibrar([400, 200, 400]);
      if (!parouSom && !bipe) { tocarBipe(); bipe = setInterval(tocarBipe, 3000); }
    }
    return;
  }
  pararBipe();

  if (s.status === "atendendo") {
    app.innerHTML = `<p class="fila-olho">Fila de chegada</p><h1 class="fila-titulo">Bom corte!</h1>
      <p class="fila-texto">Você está na cadeira com ${esc(s.barbeiro)}.</p>`;
    return;
  }
  if (s.status === "concluido") {
    pararConsulta();
    esquecerToken();
    app.innerHTML = `<p class="fila-olho">Fila de chegada</p><h1 class="fila-titulo">Valeu!</h1>
      <p class="fila-texto">Até a próxima, no padrão JP.</p>`;
    return;
  }
  if (s.status === "saiu") { esquecerToken(); mostrarFim("Você saiu da fila", "Se mudar de ideia, é só entrar de novo."); return; }
  if (s.status === "nao_veio") {
    esquecerToken();
    mostrarFim("Você perdeu a vez", "Você foi chamado e não chegou a tempo. Se ainda quiser, entre de novo no fim da fila.");
    return;
  }
  if (s.status === "removido") { esquecerToken(); mostrarFim("Você saiu da fila", "A barbearia tirou seu nome da fila. Se precisar, fale com a gente."); return; }

  // aguardando
  const perto = s.a_frente != null && s.a_frente <= 2;
  if (perto && !avisouPerto) { avisouPerto = true; vibrar(200); }
  const frente = s.a_frente === 0 ? "Você é o próximo" : `${s.a_frente} na sua frente`;
  app.innerHTML = `
    <p class="fila-olho">Fila de chegada</p>
    <h1 class="fila-titulo">Você está na fila</h1>
    ${perto ? `<p class="fila-faixa">Já vem vindo: chegue perto da barbearia.</p>` : ""}
    <div class="fila-posicao">
      <small>Sua posição</small>
      <span class="fila-numero">${s.posicao != null ? `${s.posicao}º` : "–"}</span>
      <p class="fila-detalhe">${s.espera_min == null && s.posicao != null
        ? "Seu barbeiro está indisponível agora. Fale com a barbearia."
        : `${frente} · ${textoEspera(s.espera_min)}`}</p>
      <p class="fila-detalhe"><b>${esc(s.barbeiro)}</b> · ${esc(s.servico)}</p>
    </div>
    <p class="fila-aviso">Deixe esta página aberta para ouvir o aviso quando chegar sua vez.
      Se não estiver aqui quando chamar, passa para o próximo.</p>
    <div class="fila-acoes">
      ${somLigado ? "" : `<button class="fila-btn secundario" id="fila-som">Ativar aviso sonoro</button>`}
      <button class="fila-btn secundario" id="fila-copiar">Copiar meu link</button>
      <button class="fila-btn secundario" id="fila-sair">Sair da fila</button>
    </div>`;

  // Guardar o link: abre o mesmo lugar em outro navegador ou aparelho.
  const copiar = document.getElementById("fila-copiar");
  copiar.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      copiar.textContent = "Link copiado. Guarde para voltar à fila.";
    } catch (_) {
      copiar.textContent = location.href;
    }
  });

  const som = document.getElementById("fila-som");
  if (som) som.addEventListener("click", () => { ligarSom(); tocarBipe(); som.remove(); });

  const sair = document.getElementById("fila-sair");
  sair.addEventListener("click", async () => {
    if (sair.dataset.confirmar !== "1") {
      sair.dataset.confirmar = "1";
      sair.textContent = "Sim, sair da fila";
      sair.className = "fila-btn perigo";
      return;
    }
    sair.disabled = true;
    try {
      await API.fila.sair(token);
      pararConsulta();
      esquecerToken();
      mostrarFim("Você saiu da fila", "Se mudar de ideia, é só entrar de novo.");
    } catch (e) {
      sair.disabled = false;
      sair.textContent = e.message;
    }
  });
}

function mostrarFim(titulo, texto) {
  pararConsulta();
  pararBipe();
  app.innerHTML = `
    <p class="fila-olho">Fila de chegada</p>
    <h1 class="fila-titulo">${esc(titulo)}</h1>
    <p class="fila-texto">${esc(texto)}</p>
    <button class="fila-btn" id="fila-de-novo">Entrar de novo</button>`;
  document.getElementById("fila-de-novo").addEventListener("click", mostrarEscolha);
}

/* ---------------- início ---------------- */

document.addEventListener("visibilitychange", () => {
  const token = lerToken();
  if (!document.hidden && token && consulta) consultar(token);
});

(function iniciar() {
  const token = lerToken();
  if (token) acompanhar(token);
  else mostrarEscolha();
})();
