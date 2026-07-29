/* =====================================================
   script.js
   Controla o fluxo em formato de WIZARD (passo a passo),
   no estilo da referência: Serviço → Profissional → Data
   → Horário → Dados.

   Estado do agendamento (state):
   {
     servico:   { id, nome, preco, duracao_min },
     barbeiro:  { id, nome },
     data:      "YYYY-MM-DD",
     hora:      "HH:MM",
     nome:      string,
     telefone:  string
   }
   ===================================================== */

/* -------------------- Ícones (SVG inline) -------------------- */
const ICONES = {
  tesoura: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/></svg>`,
  relogio: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  seta: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>`,
  voltar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>`,
  usuario: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  calendario: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  alerta: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
  vazio: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg>`
};

/* -------------------- Descrições auxiliares (a API não guarda descrição) -------------------- */
const DESCRICOES_SERVICO = {
  "Degradê": "Corte com transição gradual nas laterais",
  "Social": "Corte clássico, alinhado e discreto",
  "Navalhado": "Acabamento na navalha para um corte preciso",
  "Barba": "Aparo e desenho completo da barba",
  "Corte + Barba": "Combo completo: cabelo e barba no mesmo horário",
  "Sobrancelha": "Design e alinhamento da sobrancelha"
};

function descricaoServico(nome) {
  return DESCRICOES_SERVICO[nome] || "Serviço profissional com atenção aos detalhes.";
}

/* -------------------- Passos do wizard -------------------- */
const PASSOS = [
  { chave: "servico",  rotulo: "Serviço" },
  { chave: "barbeiro", rotulo: "Profissional" },
  { chave: "data",     rotulo: "Data" },
  { chave: "horario",  rotulo: "Horário" },
  { chave: "dados",    rotulo: "Dados" }
];

/* -------------------- Estado global -------------------- */
const state = {
  servico: null,
  barbeiro: null,
  data: null,
  hora: null,
  nome: "",
  telefone: ""
};

let passoAtual = 0;
let concluido = false;

/* -------------------- Referências DOM -------------------- */
const elStepper = document.getElementById("stepper");
const elConteudo = document.getElementById("step-content");
// O link "Área do barbeiro" agora aponta direto pro painel (admin.html).


/* =====================================================
   RENDERIZAÇÃO DO STEPPER (barra de progresso no topo)
   ===================================================== */
function renderStepper() {
  elStepper.innerHTML = "";

  PASSOS.forEach((passo, i) => {
    const step = document.createElement("div");
    let classe = "step";
    if (i === passoAtual) classe += " ativo";
    else if (i < passoAtual || concluido) classe += " concluido";
    if (i < passoAtual) classe += " clickable";
    step.className = classe;

    const circulo = document.createElement("div");
    circulo.className = "step-circle";
    circulo.textContent = String(i + 1); // sempre o número do passo (sem check)

    const label = document.createElement("span");
    label.className = "step-label";
    label.textContent = passo.rotulo;

    step.appendChild(circulo);
    step.appendChild(label);

    if (i < passoAtual && !concluido) {
      step.addEventListener("click", () => irParaPasso(i));
    }

    elStepper.appendChild(step);

    if (i < PASSOS.length - 1) {
      const linha = document.createElement("div");
      linha.className = "step-linha" + (i < passoAtual ? " concluida" : "");
      elStepper.appendChild(linha);
    }
  });
}

function irParaPasso(indice) {
  passoAtual = indice;
  renderTudo();
}

function avancarPara(indice) {
  passoAtual = indice;
  renderTudo();
}

function renderTudo() {
  renderStepper();
  renderPassoAtual();
  window.scrollTo({ top: 0, behavior: "smooth" });
}


/* =====================================================
   HELPERS DE UI
   ===================================================== */
function cabecalhoPasso(titulo, subtitulo) {
  return `
    <h2 class="step-titulo">${titulo}</h2>
    <p class="step-subtitulo">${subtitulo}</p>
  `;
}

function botaoVoltar(aoClicar) {
  const btn = document.createElement("button");
  btn.className = "btn-voltar";
  btn.innerHTML = `${ICONES.voltar} Voltar`;
  btn.addEventListener("click", aoClicar);
  return btn;
}

function mostrarCarregando(container, texto = "Carregando...") {
  container.innerHTML = `<div class="carregando"><div class="spinner"></div>${texto}</div>`;
}

function mostrarErro(container, mensagem, aoTentarNovamente) {
  const box = document.createElement("div");
  box.className = "alerta-erro";
  box.innerHTML = `${ICONES.alerta} <span>${mensagem}</span>`;
  container.prepend(box);
}


/* =====================================================
   PASSO 1 — SERVIÇO
   ===================================================== */
async function renderPassoServico() {
  elConteudo.innerHTML = cabecalhoPasso("Escolha o serviço", "Selecione o que você deseja hoje");

  const lista = document.createElement("div");
  lista.className = "lista-cards";
  mostrarCarregando(lista);
  elConteudo.appendChild(lista);

  try {
    const servicos = await carregarServicos();
    lista.innerHTML = "";

    servicos.forEach((s) => {
      const card = document.createElement("button");
      card.className = "card-opcao";
      card.innerHTML = `
        <div class="card-icone">${ICONES.tesoura}</div>
        <div class="card-corpo">
          <div class="card-titulo-linha">
            <span class="card-nome">${s.nome}</span>
          </div>
          <div class="card-meta">${ICONES.relogio} ${s.duracao_min} min</div>
          <div class="card-desc">${descricaoServico(s.nome)}</div>
        </div>
        <div class="card-direita">
          <span class="card-preco">R$ ${s.preco.toFixed(2).replace(".", ",")}</span>
          <span class="card-seta">${ICONES.seta}</span>
        </div>
      `;
      card.addEventListener("click", () => {
        state.servico = s;
        avancarPara(1);
      });
      lista.appendChild(card);
    });
  } catch (erro) {
    lista.innerHTML = "";
    mostrarErro(elConteudo, erro.message);
  }
}


/* =====================================================
   PASSO 2 — PROFISSIONAL (barbeiro)
   ===================================================== */
async function renderPassoBarbeiro() {
  elConteudo.innerHTML = "";
  elConteudo.appendChild(botaoVoltar(() => irParaPasso(0)));
  elConteudo.insertAdjacentHTML("beforeend", cabecalhoPasso("Escolha o profissional", "Com quem você quer agendar"));

  const lista = document.createElement("div");
  lista.className = "lista-cards";
  mostrarCarregando(lista);
  elConteudo.appendChild(lista);

  try {
    const barbeiros = await carregarBarbeiros();
    lista.innerHTML = "";

    barbeiros.forEach((b) => {
      const card = document.createElement("button");
      card.className = "card-opcao simples";
      card.innerHTML = `
        <div class="card-icone">${ICONES.usuario}</div>
        <div class="card-corpo">
          <div class="card-titulo-linha">
            <span class="card-nome">${b.nome}</span>
          </div>
        </div>
        <div class="card-direita">
          <span class="card-seta">${ICONES.seta}</span>
        </div>
      `;
      card.addEventListener("click", () => {
        state.barbeiro = b;
        avancarPara(2);
      });
      lista.appendChild(card);
    });
  } catch (erro) {
    lista.innerHTML = "";
    mostrarErro(elConteudo, erro.message);
  }
}


/* =====================================================
   PASSO 3 — DATA

   Assim que a pessoa escolhe uma data, já checamos em segundo
   plano se existe horário livre naquele dia (antes de clicar
   em "Ver horários"). Se não tiver, o botão fica cinza e avisa
   por escrito — porque o calendário em si é o componente nativo
   do navegador, e não temos como "pintar" dias sem vaga nele.
   ===================================================== */
/* Quantos dias a faixa mostra pra frente e rótulos curtos do topo do card. */
const DIAS_A_MOSTRAR = 14;
const DIAS_SEMANA_CURTO = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];

/* Dias da semana fechados no FRONT (0=domingo). Espelha o padrão de
   DIAS_FECHADOS no .env do backend — serve só pra deixar os dias apagados
   na faixa. Quem manda de verdade continua sendo o backend + a checagem de
   disponibilidade; se mudar DIAS_FECHADOS no .env, ajuste aqui também. */
const DIAS_FECHADOS_FRONT = [0];

function diaEstaFechado(dataObj) {
  return DIAS_FECHADOS_FRONT.includes(dataObj.getDay());
}

function renderPassoData() {
  elConteudo.innerHTML = "";
  elConteudo.appendChild(botaoVoltar(() => irParaPasso(1)));
  elConteudo.insertAdjacentHTML("beforeend", cabecalhoPasso("Escolha a data", `Para o serviço de ${state.servico.nome} com ${state.barbeiro.nome}`));

  const bloco = document.createElement("div");
  bloco.className = "bloco-data";

  const chips = document.createElement("div");
  chips.className = "chips-rapidos";

  const faixa = document.createElement("div");
  faixa.className = "faixa-dias";

  const avisoChecagem = document.createElement("p");
  avisoChecagem.className = "aviso-checagem-data";

  bloco.appendChild(chips);
  bloco.appendChild(faixa);
  bloco.appendChild(avisoChecagem);
  elConteudo.appendChild(bloco);

  const acoes = document.createElement("div");
  acoes.className = "acoes-passo";
  const btnContinuar = document.createElement("button");
  btnContinuar.className = "btn btn-primario";
  btnContinuar.textContent = "Ver horários disponíveis";
  btnContinuar.disabled = !state.data;
  acoes.appendChild(btnContinuar);
  elConteudo.appendChild(acoes);

  // Token evita que uma checagem antiga (de uma data que a pessoa já trocou)
  // sobrescreva o resultado de uma checagem mais nova, se as respostas
  // chegarem fora de ordem.
  let tokenChecagem = 0;

  function botaoParaEstadoPadrao() {
    btnContinuar.className = "btn btn-primario";
    btnContinuar.textContent = "Ver horários disponíveis";
    avisoChecagem.textContent = "";
  }

  async function checarDisponibilidadeDoDia() {
    const minhaChecagem = ++tokenChecagem;

    btnContinuar.disabled = true;
    btnContinuar.className = "btn btn-secundario";
    btnContinuar.textContent = "Verificando disponibilidade...";
    avisoChecagem.textContent = "";

    const horarios = await carregarHorarios(state.data, state.barbeiro.id);

    if (minhaChecagem !== tokenChecagem) return; // a pessoa já trocou de data, ignora essa resposta velha

    btnContinuar.disabled = false;

    if (!horarios || horarios.length === 0) {
      btnContinuar.className = "btn btn-secundario";
      btnContinuar.textContent = "Sem horários nesse dia";
      avisoChecagem.textContent = "Ao continuar, mostramos a próxima data com vaga.";
    } else {
      botaoParaEstadoPadrao();
    }
  }

  // Marca visualmente o dia/atalho escolhido e dispara a checagem.
  function selecionarData(dataISO) {
    state.data = dataISO;
    state.hora = null;
    faixa.querySelectorAll(".dia-pill").forEach((p) => {
      p.classList.toggle("selecionado", p.dataset.data === dataISO);
    });
    chips.querySelectorAll(".chip-rapido").forEach((c) => {
      c.classList.toggle("selecionado", c.dataset.data === dataISO);
    });
    checarDisponibilidadeDoDia();
  }

  // Rola a faixa trazendo o dia informado pro início (usado pelo atalho
  // "Próxima semana" e pra deixar visível a data pré-selecionada).
  function rolarFaixaPara(dataISO) {
    const alvo = faixa.querySelector(`.dia-pill[data-data="${dataISO}"]`);
    if (!alvo) return;
    const desloc = alvo.getBoundingClientRect().left - faixa.getBoundingClientRect().left;
    faixa.scrollTo({ left: faixa.scrollLeft + desloc - 4, behavior: "smooth" });
  }

  // Faixa: próximos DIAS_A_MOSTRAR dias a partir de hoje (fuso local).
  const hoje = new Date();
  for (let i = 0; i < DIAS_A_MOSTRAR; i++) {
    const d = new Date(hoje);
    d.setDate(hoje.getDate() + i);
    const iso = dataLocalISO(d);
    const fechado = diaEstaFechado(d);

    const pill = document.createElement("button");
    pill.className = "dia-pill" + (state.data === iso ? " selecionado" : "");
    pill.dataset.data = iso;
    pill.disabled = fechado;

    const topo = i === 0 ? "Hoje" : (i === 1 ? "Amanhã" : DIAS_SEMANA_CURTO[d.getDay()]);
    pill.innerHTML = `<span class="dia-pill-topo">${topo}</span><span class="dia-pill-num">${d.getDate()}</span>`;
    if (!fechado) pill.addEventListener("click", () => selecionarData(iso));
    faixa.appendChild(pill);
  }

  // Atalhos rápidos (Hoje / Amanhã), desabilitados se caírem em dia fechado.
  [["Hoje", 0], ["Amanhã", 1]].forEach(([rotulo, offset]) => {
    const d = new Date(hoje);
    d.setDate(hoje.getDate() + offset);
    const iso = dataLocalISO(d);
    const fechado = diaEstaFechado(d);

    const chip = document.createElement("button");
    chip.className = "chip-rapido" + (state.data === iso ? " selecionado" : "");
    chip.dataset.data = iso;
    chip.textContent = rotulo;
    chip.disabled = fechado;
    if (!fechado) chip.addEventListener("click", () => selecionarData(iso));
    chips.appendChild(chip);
  });

  // Atalho "Próxima semana": não seleciona uma data, só ROLA a faixa até a
  // segunda-feira da semana que vem, pra pessoa escolher o dia por lá.
  const proxSegunda = new Date(hoje);
  const diasAteSegunda = ((8 - hoje.getDay()) % 7) || 7; // sempre a segunda da semana seguinte
  proxSegunda.setDate(hoje.getDate() + diasAteSegunda);
  const proxSegundaISO = dataLocalISO(proxSegunda);

  const chipProx = document.createElement("button");
  chipProx.className = "chip-rapido chip-navegacao";
  chipProx.textContent = "Próxima semana";
  chipProx.addEventListener("click", () => rolarFaixaPara(proxSegundaISO));
  chips.appendChild(chipProx);

  btnContinuar.addEventListener("click", () => {
    if (!state.data) return;
    btnContinuar.disabled = true; // trava contra clique duplo enquanto a tela troca
    avancarPara(3);
  });

  // Escolhe automaticamente o primeiro dia com vaga a partir de hoje, pulando
  // dias fechados (domingo) e dias sem horário (fim do dia / lotado). Na prática
  // isso vira "Hoje" por padrão, ou "amanhã"/próximo dia útil quando hoje não dá.
  async function preSelecionarMelhorData() {
    btnContinuar.disabled = true;
    btnContinuar.className = "btn btn-secundario";
    btnContinuar.textContent = "Verificando disponibilidade...";

    const meuToken = ++tokenChecagem; // se a pessoa clicar em outro dia, aborta
    let escolhida = null;
    const d = new Date(hoje);

    for (let i = 0; i <= LIMITE_DIAS_BUSCA_AUTOMATICA; i++) {
      if (!diaEstaFechado(d)) {
        const iso = dataLocalISO(d);
        const horarios = await carregarHorarios(iso, state.barbeiro.id);
        if (meuToken !== tokenChecagem) return; // a pessoa já escolheu manualmente
        if (horarios && horarios.length > 0) { escolhida = iso; break; }
      }
      d.setDate(d.getDate() + 1);
    }

    // Nenhum dia com vaga no período: cai no primeiro dia aberto mesmo assim
    // (o passo de horário ainda tenta achar vaga adiante).
    if (!escolhida) {
      const f = new Date(hoje);
      while (diaEstaFechado(f)) f.setDate(f.getDate() + 1);
      escolhida = dataLocalISO(f);
    }

    selecionarData(escolhida);
    rolarFaixaPara(escolhida);
  }

  // Se já tinha uma data escolhida antes (ex: voltou pra esse passo), mantém e
  // só reconfere. Senão, pré-seleciona a melhor data automaticamente.
  if (state.data) {
    checarDisponibilidadeDoDia();
  } else {
    preSelecionarMelhorData();
  }
}


/* =====================================================
   PASSO 4 — HORÁRIO

   Se a data escolhida não tiver mais nenhum horário livre
   (ex: já passou tudo do dia, ou o barbeiro está lotado),
   busca automaticamente os dias seguintes até achar um com
   vaga, em vez de simplesmente dizer "não há horários".
   ===================================================== */
const LIMITE_DIAS_BUSCA_AUTOMATICA = 14; // não procura pra sempre, tem um teto

async function renderPassoHorario() {
  elConteudo.innerHTML = "";
  elConteudo.appendChild(botaoVoltar(() => irParaPasso(2)));
  elConteudo.insertAdjacentHTML("beforeend", cabecalhoPasso("Escolha o horário", "Buscando a próxima disponibilidade..."));

  const areaHorarios = document.createElement("div");
  mostrarCarregando(areaHorarios, "Buscando horários disponíveis...");
  elConteudo.appendChild(areaHorarios);

  try {
    let dataUsada = state.data;
    let horarios = await carregarHorarios(dataUsada, state.barbeiro.id);

    let pulouDeDia = false;
    let tentativas = 0;

    // Enquanto não achar horário livre, tenta o dia seguinte
    // (até o limite de tentativas, pra não ficar buscando infinitamente).
    while ((!horarios || horarios.length === 0) && tentativas < LIMITE_DIAS_BUSCA_AUTOMATICA) {
      dataUsada = adicionarDias(dataUsada, 1);
      horarios = await carregarHorarios(dataUsada, state.barbeiro.id);
      pulouDeDia = true;
      tentativas++;
    }

    // Atualiza o subtítulo agora que já sabemos qual data será exibida
    const subtitulo = elConteudo.querySelector(".step-subtitulo");
    if (subtitulo) {
      subtitulo.innerHTML = `Disponibilidade de ${state.barbeiro.nome} em <span class="data-destaque">${formatarDataBR(dataUsada)}</span>`;
    }

    if (!horarios || horarios.length === 0) {
      // Mesmo pulando vários dias, não achou nada livre
      areaHorarios.innerHTML = `
        <div class="vazio-horarios">
          ${ICONES.vazio}
          <p>Não encontramos horários livres com ${state.barbeiro.nome} nos próximos dias.<br>Tente outra data manualmente.</p>
        </div>
      `;
      const acoes = document.createElement("div");
      acoes.className = "acoes-passo";
      const btnOutraData = document.createElement("button");
      btnOutraData.className = "btn btn-primario";
      btnOutraData.textContent = "Escolher outra data";
      btnOutraData.addEventListener("click", () => irParaPasso(2));
      acoes.appendChild(btnOutraData);
      elConteudo.appendChild(acoes);
      return;
    }

    // Se pulou de dia, guarda a nova data no estado e avisa o cliente
    // (de um jeito discreto: o dia original aparece "cinza"/riscado,
    // não como uma caixa de erro — porque não é um erro, é só um aviso).
    if (pulouDeDia) {
      const dataOriginalFormatada = formatarDataBR(state.data);
      state.data = dataUsada;
      state.hora = null;

      const aviso = document.createElement("div");
      aviso.className = "aviso-dia-pulado";
      aviso.innerHTML = `
        <span class="chip-dia-cinza">${dataOriginalFormatada}</span>
        <span class="aviso-seta">${ICONES.seta}</span>
        <span>sem horários — mostrando o próximo dia com vaga</span>
      `;
      areaHorarios.before(aviso);
    }

    const grid = document.createElement("div");
    grid.className = "grid-horarios";

    horarios.forEach((h) => {
      const chip = document.createElement("button");
      chip.className = "chip-horario" + (state.hora === h ? " selecionado" : "");
      chip.textContent = h;
      chip.addEventListener("click", () => {
        state.hora = h;
        avancarPara(4);
      });
      grid.appendChild(chip);
    });

    areaHorarios.innerHTML = "";
    areaHorarios.appendChild(grid);

  } catch (erro) {
    areaHorarios.innerHTML = "";
    mostrarErro(elConteudo, erro.message);
  }
}


/* =====================================================
   PASSO 5 — DADOS DO CLIENTE + CONFIRMAÇÃO
   ===================================================== */
function renderPassoDados() {
  elConteudo.innerHTML = "";
  elConteudo.appendChild(botaoVoltar(() => irParaPasso(3)));
  elConteudo.insertAdjacentHTML("beforeend", cabecalhoPasso("Seus dados", "Falta pouco! Confirme suas informações"));

  const form = document.createElement("div");
  form.className = "form-dados";
  form.innerHTML = `
    <div class="campo-grupo">
      <label class="campo-label" for="input-nome">Nome completo</label>
      <input type="text" id="input-nome" placeholder="Digite seu nome" value="${state.nome || ""}" />
    </div>
    <div class="campo-grupo">
      <label class="campo-label" for="input-telefone">Telefone (opcional)</label>
      <input type="tel" id="input-telefone" placeholder="(11) 99999-9999" value="${state.telefone || ""}" />
    </div>
  `;
  elConteudo.appendChild(form);

  const resumo = document.createElement("div");
  resumo.className = "resumo-card";
  resumo.innerHTML = `
    <p class="resumo-titulo">Resumo do agendamento</p>
    <div class="resumo-linha"><span class="chave">Serviço</span><span class="valor">${state.servico.nome}</span></div>
    <div class="resumo-linha"><span class="chave">Profissional</span><span class="valor">${state.barbeiro.nome}</span></div>
    <div class="resumo-linha"><span class="chave">Data</span><span class="valor">${formatarDataBR(state.data)}</span></div>
    <div class="resumo-linha"><span class="chave">Horário</span><span class="valor">${state.hora}</span></div>
    <div class="resumo-linha total"><span class="chave">Valor</span><span class="valor">R$ ${state.servico.preco.toFixed(2).replace(".", ",")}</span></div>
  `;
  elConteudo.appendChild(resumo);

  const acoes = document.createElement("div");
  acoes.className = "acoes-passo";

  const btnConfirmar = document.createElement("button");
  btnConfirmar.className = "btn btn-primario";
  btnConfirmar.textContent = "Confirmar agendamento";
  btnConfirmar.disabled = !(state.nome && state.nome.trim());

  const inputNome = form.querySelector("#input-nome");
  const inputTelefone = form.querySelector("#input-telefone");

  inputNome.addEventListener("input", () => {
    state.nome = inputNome.value;
    btnConfirmar.disabled = !inputNome.value.trim();
  });
  inputTelefone.addEventListener("input", () => {
    state.telefone = inputTelefone.value;
  });

  btnConfirmar.addEventListener("click", finalizarAgendamento);

  acoes.appendChild(btnConfirmar);
  elConteudo.appendChild(acoes);
}

async function finalizarAgendamento() {
  const btn = elConteudo.querySelector(".btn-primario");
  const btnVoltar = elConteudo.querySelector(".btn-voltar");
  if (btn) { btn.disabled = true; btn.textContent = "Enviando..."; }
  if (btnVoltar) btnVoltar.style.display = "none";

  try {
    await API.criarAgendamento({
      nome_cliente: state.nome.trim(),
      telefone: state.telefone.trim() || null,
      servico_id: state.servico.id,
      barbeiro_id: state.barbeiro.id,
      data: state.data,
      hora: state.hora
    });

    concluido = true;
    renderStepper();
    renderSucesso();

  } catch (erro) {
    mostrarErro(elConteudo, erro.message);
    if (btn) { btn.disabled = false; btn.textContent = "Confirmar agendamento"; }
    if (btnVoltar) btnVoltar.style.display = "";
  }
}


/* =====================================================
   TELA DE SUCESSO

   Fica visível com uma barra de progresso preenchendo em
   ~15s (dá tempo do cliente ler com calma); quando termina,
   volta sozinha pro início (bom pra um totem/tablet na loja
   sendo usado por vários clientes seguidos). O botão "OK"
   deixa pular a espera na hora, se quiser.
   ===================================================== */
const DURACAO_TELA_SUCESSO_SEGUNDOS = 15;

function renderSucesso() {
  elConteudo.innerHTML = `
    <div class="tela-sucesso">
      <div class="icone-sucesso">${ICONES.check}</div>
      <h2>Agendamento confirmado!</h2>
      <p>Prontinho, ${state.nome}! Seu horário de <strong>${state.servico.nome}</strong> com <strong>${state.barbeiro.nome}</strong>
      no dia ${formatarDataBR(state.data)} às ${state.hora} está garantido. Te esperamos!</p>
      <div class="barra-progresso-container">
        <div class="barra-progresso-preenchimento" id="barra-progresso-sucesso"></div>
      </div>
      <button class="btn btn-primario" id="btn-ok-sucesso" style="max-width:200px;margin:0 auto;">OK</button>
    </div>
  `;

  const barra = document.getElementById("barra-progresso-sucesso");
  const btnOk = document.getElementById("btn-ok-sucesso");

  const reiniciar = () => location.reload();

  // A barra CSS já roda sozinha (ver .barra-progresso-preenchimento no style.css);
  // só precisamos saber quando ela termina de encher, pra reiniciar a tela.
  barra.style.animationDuration = `${DURACAO_TELA_SUCESSO_SEGUNDOS}s`;
  barra.addEventListener("animationend", reiniciar);
  btnOk.addEventListener("click", reiniciar); // deixa pular a espera
}


/* =====================================================
   ROTEADOR DE PASSOS
   ===================================================== */
function renderPassoAtual() {
  switch (PASSOS[passoAtual].chave) {
    case "servico":  renderPassoServico(); break;
    case "barbeiro": renderPassoBarbeiro(); break;
    case "data":      renderPassoData(); break;
    case "horario":   renderPassoHorario(); break;
    case "dados":     renderPassoDados(); break;
  }
}


/* =====================================================
   FORMATAÇÃO
   ===================================================== */
function formatarDataBR(dataISO) {
  const [ano, mes, dia] = dataISO.split("-");
  return `${dia}/${mes}/${ano}`;
}

/** Soma `n` dias a uma data "YYYY-MM-DD" e devolve no mesmo formato. */
function adicionarDias(dataISO, n) {
  const [ano, mes, dia] = dataISO.split("-").map(Number);
  const d = new Date(ano, mes - 1, dia); // mês em JS começa em 0
  d.setDate(d.getDate() + n);

  const anoNovo = d.getFullYear();
  const mesNovo = String(d.getMonth() + 1).padStart(2, "0");
  const diaNovo = String(d.getDate()).padStart(2, "0");
  return `${anoNovo}-${mesNovo}-${diaNovo}`;
}

/**
 * Devolve a data ATUAL no formato "YYYY-MM-DD", usando o fuso horário
 * LOCAL do navegador (não UTC).
 *
 * IMPORTANTE: não use `new Date().toISOString().split("T")[0]` pra isso.
 * toISOString() sempre converte pra UTC — no fuso do Brasil (UTC-3),
 * isso faz a "data de hoje" virar amanhã a partir das 21h. Um cliente
 * agendando às 22h veria os horários de hoje não sendo filtrados
 * corretamente, porque a função ia comparar "hoje" (21/07, em UTC)
 * com a data que ele escolheu no calendário (20/07, no fuso dele).
 */
function dataLocalISO(data = new Date()) {
  const ano = data.getFullYear();
  const mes = String(data.getMonth() + 1).padStart(2, "0");
  const dia = String(data.getDate()).padStart(2, "0");
  return `${ano}-${mes}-${dia}`;
}


/* =====================================================
   COOLDOWN — antecedência mínima pra agendar

   Isso é uma segunda camada de proteção: o backend (app.py,
   variável ANTECEDENCIA_MINIMA_MINUTOS no .env) já é quem
   decide de verdade quais horários são válidos. Esse filtro
   aqui é só uma rede de segurança no front-end, útil também
   quando o mockData.js entra em ação (ele não sabe que horas
   são agora, então sem isso um horário "09:00" apareceria
   disponível mesmo depois do meio-dia).

   IMPORTANTE: mantenha COOLDOWN_MINUTOS igual ao valor de
   ANTECEDENCIA_MINIMA_MINUTOS no .env do backend, senão os
   dois lados vão discordar sobre o que é "muito em cima da hora".
   ===================================================== */
const COOLDOWN_MINUTOS = 30;

function filtrarHorariosFuturos(horarios, dataISO) {
  const agora = new Date();
  const hojeISO = dataLocalISO(agora);

  // Se a data não é hoje, nenhum horário "já passou" — devolve tudo.
  if (dataISO !== hojeISO) return horarios;

  const limiteMin = agora.getHours() * 60 + agora.getMinutes() + COOLDOWN_MINUTOS;

  return horarios.filter((h) => {
    const [hh, mm] = h.split(":").map(Number);
    return (hh * 60 + mm) > limiteMin;
  });
}


/* =====================================================
   CARREGAMENTO (API real, com fallback pro mock)
   ===================================================== */
async function carregarServicos() {
  try {
    return await API.listarServicos();
  } catch (erro) {
    console.warn("Backend offline, usando mock.", erro.message);
    return MOCK_SERVICOS;
  }
}

async function carregarBarbeiros() {
  try {
    return await API.listarBarbeiros();
  } catch (erro) {
    console.warn("Backend offline, usando mock.", erro.message);
    return MOCK_BARBEIROS;
  }
}

async function carregarHorarios(data, barbeiroId) {
  let horarios;
  try {
    const resultado = await API.listarHorariosDisponiveis(data, barbeiroId);
    horarios = resultado.horarios_disponiveis;
  } catch (erro) {
    console.warn("Backend offline, usando mock.", erro.message);
    horarios = MOCK_HORARIOS;
  }

  // Filtra de novo aqui no front, como segunda camada de proteção
  // (ver explicação na seção "COOLDOWN" acima).
  return filtrarHorariosFuturos(horarios, data);
}


/* =====================================================
   START
   ===================================================== */
renderTudo();
