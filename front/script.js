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
  { chave: "servico", rotulo: "Serviço" },
  { chave: "barbeiro", rotulo: "Profissional" },
  { chave: "data", rotulo: "Data" },
  { chave: "horario", rotulo: "Horário" },
  { chave: "dados", rotulo: "Dados" }
];

/* -------------------- Estado global -------------------- */
const state = {
  servico: null,
  barbeiro: null,
  data: null,
  hora: null,
  nome: "",
  telefone: "",
  email: "",
  linkAgenda: ""     // link do Google Agenda, devolvido pelo back na confirmação
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
  container.innerHTML = `
    <div class="carregando">
      <div class="spinner"></div>
      <span>${texto}</span>
      <small class="carregando-aviso" style="display:none;">Iniciando o servidor… a primeira visita do dia pode levar até ~1 min. Aguarde.</small>
    </div>`;
  // Se a carga demorar (cold start do backend grátis), mostra o aviso — mas só
  // se o spinner ainda estiver na tela (carga rápida já terá sido substituída).
  const aviso = container.querySelector(".carregando-aviso");
  setTimeout(() => { if (aviso && aviso.isConnected) aviso.style.display = "block"; }, 4000);
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
      const icone = s.imagem
        ? `<div class="card-icone card-icone-foto"><img src="img/${s.imagem}" alt="${s.nome}" loading="lazy"></div>`
        : `<div class="card-icone">${ICONES.tesoura}</div>`;
      card.innerHTML = `
        ${icone}
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
      const icone = b.foto
        ? `<div class="card-icone card-icone-foto"><img src="img/${b.foto}" alt="${b.nome}" loading="lazy"></div>`
        : `<div class="card-icone">${ICONES.usuario}</div>`;
      card.innerHTML = `
        ${icone}
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
/* Quantos dias a faixa mostra pra frente e rótulos curtos do topo do card.
   8 = hoje + 7 dias (janela de 1 semana). Deve casar com LIMITE_DIAS_AGENDAMENTO
   no backend (app.py); se mudar lá, ajuste aqui também. */
const DIAS_A_MOSTRAR = 8;
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

  // Estado do botão pra uma quantidade de vagas já conhecida — evita perguntar
  // de novo ao servidor um dia que a varredura acabou de conferir.
  function aplicarEstadoDoBotao(quantidade) {
    btnContinuar.disabled = false;
    if (!quantidade) {
      // O botão diz o que ELE faz, não o que falta. "Sem horários nesse dia"
      // parece um fim de linha; na verdade o passo seguinte busca a próxima
      // data com vaga sozinho.
      btnContinuar.className = "btn btn-primario";
      btnContinuar.textContent = "Ver próxima data com vaga";
      avisoChecagem.textContent = `${state.barbeiro.nome} não tem horário em ${formatarDataBR(state.data)}.`;
    } else {
      botaoParaEstadoPadrao();
    }
  }

  // Consulta o servidor pelo dia escolhido na mão (não passou pela varredura).
  async function checarDisponibilidadeDoDia() {
    const minhaChecagem = ++tokenChecagem;

    btnContinuar.disabled = true;
    btnContinuar.className = "btn btn-secundario";
    btnContinuar.textContent = "Verificando disponibilidade...";
    avisoChecagem.textContent = "";

    const horarios = await carregarHorarios(state.data, state.barbeiro.id);

    if (minhaChecagem !== tokenChecagem) return; // trocou de data: resposta velha
    aplicarEstadoDoBotao(horarios ? horarios.length : 0);
  }

  // Marca visualmente o dia/atalho escolhido. Quando a varredura já sabe quantas
  // vagas aquele dia tem, passa o número e economiza uma ida ao servidor.
  function selecionarData(dataISO, vagasConhecidas) {
    state.data = dataISO;
    state.hora = null;
    faixa.querySelectorAll(".dia-pill").forEach((p) => {
      p.classList.toggle("selecionado", p.dataset.data === dataISO);
    });
    chips.querySelectorAll(".chip-rapido").forEach((c) => {
      c.classList.toggle("selecionado", c.dataset.data === dataISO);
    });
    if (typeof vagasConhecidas === "number") {
      avisoChecagem.textContent = "";
      aplicarEstadoDoBotao(vagasConhecidas);
    } else {
      checarDisponibilidadeDoDia();
    }
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

  // Confere TODOS os dias visíveis de uma vez e usa o resultado pra duas coisas:
  // marcar os dias sem vaga e já escolher a primeira data com horário.
  //
  // Antes eram duas varreduras: uma sequencial, que perguntava um dia, esperava,
  // perguntava o próximo (é o "foi limpando quinta, sexta, sábado..."), e outra
  // em paralelo só pra marcar. Cada consulta custa ~1s, então procurar de quinta
  // até terça levava ~8 segundos com os dias acinzentando um a um.
  // Agora é uma rodada só, em paralelo.
  //
  // Dia só é marcado como sem vaga quando a resposta CHEGA e vem vazia. Se a
  // consulta falhar, o dia fica como está: bloquear um dia bom por causa de rede
  // ruim impediria de agendar, que é pior que o incômodo original.
  async function prepararDias(manterData) {
    const meuToken = ++tokenChecagem;
    btnContinuar.disabled = true;
    btnContinuar.className = "btn btn-secundario";
    btnContinuar.textContent = "Verificando disponibilidade...";

    const servicoId = state.servico ? state.servico.id : null;
    const pills = Array.from(faixa.querySelectorAll(".dia-pill:not([disabled])"));

    const dias = await Promise.all(pills.map(async (pill) => {
      try {
        const r = await API.listarHorariosDisponiveis(pill.dataset.data, state.barbeiro.id, servicoId);
        const vagas = r && r.horarios_disponiveis;
        return { pill, data: pill.dataset.data, vagas: Array.isArray(vagas) ? vagas.length : null };
      } catch (_) {
        return { pill, data: pill.dataset.data, vagas: null };   // não deu pra saber
      }
    }));

    if (meuToken !== tokenChecagem) return;   // a pessoa já escolheu um dia na mão

    // Respeita a data que a pessoa já tinha escolhido; só decide sozinho quando
    // ela ainda não escolheu nada.
    const jaEscolhida = manterData ? dias.find((d) => d.data === manterData) : null;
    const escolhida = jaEscolhida || dias.find((d) => d.vagas > 0) || dias[0];

    dias.forEach((d) => {
      // O dia que vai ficar selecionado nunca é desabilitado — travar o que está
      // embaixo do dedo da pessoa seria pior do que deixar clicável.
      if (d.vagas === 0 && d !== escolhida) {
        d.pill.classList.add("sem-vaga");
        d.pill.title = `${state.barbeiro.nome} não tem horário nesse dia`;
        d.pill.disabled = true;
      }
    });

    if (!escolhida) { botaoParaEstadoPadrao(); btnContinuar.disabled = false; return; }
    selecionarData(escolhida.data, escolhida.vagas === null ? undefined : escolhida.vagas);
    rolarFaixaPara(escolhida.data);
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

  btnContinuar.addEventListener("click", () => {
    if (!state.data) return;
    btnContinuar.disabled = true; // trava contra clique duplo enquanto a tela troca
    avancarPara(3);
  });

  // Uma varredura só resolve os dois casos: se a pessoa já tinha data escolhida
  // (voltou pro passo), mantém a dela; senão, pega a primeira com vaga.
  prepararDias(state.data);
}


/* =====================================================
   PASSO 4 — HORÁRIO

   Se a data escolhida não tiver mais nenhum horário livre
   (ex: já passou tudo do dia, ou o barbeiro está lotado),
   busca automaticamente os dias seguintes até achar um com
   vaga, em vez de simplesmente dizer "não há horários".
   ===================================================== */
const LIMITE_DIAS_BUSCA_AUTOMATICA = 7; // teto = janela de 1 semana (hoje + 7)

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
      <label class="campo-label" for="input-telefone">Whatsapp <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.82 11.82 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884a9.86 9.86 0 001.51 5.26l-.999 3.648 3.489-.919zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"></path></svg></label>
      <input type="tel" id="input-telefone" inputmode="numeric" placeholder="11999998888" value="${state.telefone || ""}" />
    </div>
    <div class="campo-grupo">
      <label class="campo-label" for="input-email">E-mail <span class="campo-opcional">(opcional)</span></label>
      <input type="email" id="input-email" inputmode="email" autocomplete="email"
             placeholder="voce@email.com" value="${state.email || ""}" />
      <p class="campo-nota" id="email-nota">Sem e-mail você não recebe a confirmação nem o lembrete do horário.</p>
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
  btnConfirmar.disabled = true; // fica cinza até nome + telefone válidos

  const inputNome = form.querySelector("#input-nome");
  const inputTelefone = form.querySelector("#input-telefone");
  const inputEmail = form.querySelector("#input-email");

  // Telefone válido = 10 ou 11 dígitos (DDD + número), ignorando o que não for número.
  function telefoneValido(v) {
    return /^\d{10,11}$/.test(v.replace(/\D/g, ""));
  }
  // E-mail é OPCIONAL: vazio passa. Só barra se a pessoa digitar algo inválido,
  // pra não mandar confirmação e lembrete pro vazio.
  function emailValido(v) {
    const email = (v || "").trim();
    return email === "" || /^[^@\s]+@[^@\s.]+\.[^@\s]+$/.test(email);
  }
  const notaEmail = form.querySelector("#email-nota");
  // A nota some quando a pessoa preenche — serve de lembrete, não de cobrança.
  function atualizarNotaEmail() {
    notaEmail.hidden = inputEmail.value.trim() !== "";
  }
  // Libera o botão com nome e telefone; o e-mail só não pode estar errado.
  function atualizarBotao() {
    btnConfirmar.disabled = !(inputNome.value.trim()
      && telefoneValido(inputTelefone.value)
      && emailValido(inputEmail.value));
  }

  inputNome.addEventListener("input", () => { state.nome = inputNome.value; atualizarBotao(); });
  inputTelefone.addEventListener("input", () => { state.telefone = inputTelefone.value; atualizarBotao(); });
  inputEmail.addEventListener("input", () => {
    state.email = inputEmail.value; atualizarNotaEmail(); atualizarBotao();
  });
  atualizarNotaEmail();

  btnConfirmar.addEventListener("click", finalizarAgendamento);
  atualizarBotao(); // caso a pessoa tenha voltado pro passo com os campos já preenchidos

  acoes.appendChild(btnConfirmar);
  elConteudo.appendChild(acoes);
}

async function finalizarAgendamento() {
  const btn = elConteudo.querySelector(".btn-primario");
  const btnVoltar = elConteudo.querySelector(".btn-voltar");
  if (btn) { btn.disabled = true; btn.textContent = "Enviando..."; }
  if (btnVoltar) btnVoltar.style.display = "none";

  try {
    const criado = await API.criarAgendamento({
      nome_cliente: state.nome.trim(),
      telefone: state.telefone.trim(),
      email: (state.email || "").trim(),
      servico_id: state.servico.id,
      barbeiro_id: state.barbeiro.id,
      data: state.data,
      hora: state.hora
    });

    // O back devolve o link do Google Agenda — o lembrete vira o alarme do
    // próprio celular do cliente.
    state.linkAgenda = (criado && criado.link_agenda) || "";

    concluido = true;
    renderStepper();
    renderSucesso();

  } catch (erro) {
    // A validação de preenchimento fica no botão; aqui mostramos só erros
    // reais do envio (ex: horário tomado numa corrida — 409).
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
  // Botão de calendário: o cliente toca e o horário entra na agenda do celular
  // dele — o alarme passa a ser do próprio aparelho, que ele sempre vê.
  const botaoAgenda = state.linkAgenda
    ? `<a class="btn btn-agenda" id="btn-agenda" href="${state.linkAgenda}" target="_blank" rel="noopener">
         ${ICONES.calendario} Adicionar à minha agenda
       </a>`
    : "";

  elConteudo.innerHTML = `
    <div class="tela-sucesso">
      <div class="icone-sucesso">${ICONES.check}</div>
      <h2>Agendamento confirmado!</h2>
      <p>Prontinho, ${state.nome}! Seu horário de <strong>${state.servico.nome}</strong> com <strong>${state.barbeiro.nome}</strong>
      no dia ${formatarDataBR(state.data)} às ${state.hora} está garantido. Te esperamos!</p>
      ${botaoAgenda}
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
  // Com o botão de agenda na tela, dá mais tempo pra pessoa tocar nele.
  const segundos = state.linkAgenda
    ? DURACAO_TELA_SUCESSO_SEGUNDOS * 2
    : DURACAO_TELA_SUCESSO_SEGUNDOS;
  barra.style.animationDuration = `${segundos}s`;
  barra.addEventListener("animationend", reiniciar);
  btnOk.addEventListener("click", reiniciar); // deixa pular a espera

  // Se a pessoa foi adicionar na agenda, não reinicia a tela por baixo dela.
  const linkAgenda = document.getElementById("btn-agenda");
  if (linkAgenda) {
    linkAgenda.addEventListener("click", () => barra.removeEventListener("animationend", reiniciar));
  }
}


/* =====================================================
   ROTEADOR DE PASSOS
   ===================================================== */
function renderPassoAtual() {
  switch (PASSOS[passoAtual].chave) {
    case "servico": renderPassoServico(); break;
    case "barbeiro": renderPassoBarbeiro(); break;
    case "data": renderPassoData(); break;
    case "horario": renderPassoHorario(); break;
    case "dados": renderPassoDados(); break;
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
const COOLDOWN_MINUTOS = 15;

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
   CARREGAMENTO (API real)

   Em PRODUÇÃO não usamos mais o mock: se o backend falhar, a gente
   RE-TENTA (o host grátis do Render hiberna após ~15min e a 1ª
   requisição do dia pode demorar ~40s pra acordar). Assim o cliente
   vê "carregando" e recebe dados REAIS, nunca dados falsos.

   O mock (mockData.js) só entra em DESENVOLVIMENTO local, como
   conveniência quando o backend está desligado.
   ===================================================== */
const EH_LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
const RETRY_MAX = 8;            // ~ cobre o cold start do Render
const RETRY_ESPERA_MS = 5000;

function esperar(ms) { return new Promise((r) => setTimeout(r, ms)); }

/** Chama uma função de API re-tentando algumas vezes (pro cold start). */
async function chamarComRetry(fn) {
  let ultimoErro;
  for (let tentativa = 1; tentativa <= RETRY_MAX; tentativa++) {
    try {
      return await fn();
    } catch (erro) {
      ultimoErro = erro;
      if (tentativa < RETRY_MAX) await esperar(RETRY_ESPERA_MS);
    }
  }
  throw ultimoErro;
}

async function carregarServicos() {
  if (EH_LOCAL) {
    try { return await API.listarServicos(); }
    catch (erro) { console.warn("Backend offline (dev): usando mock.", erro.message); return MOCK_SERVICOS; }
  }
  // Produção: re-tenta (cold start) e, se falhar de vez, deixa o erro subir
  // (a tela mostra erro) — nunca dado falso.
  return await chamarComRetry(() => API.listarServicos());
}

async function carregarBarbeiros() {
  if (EH_LOCAL) {
    try { return await API.listarBarbeiros(); }
    catch (erro) { console.warn("Backend offline (dev): usando mock.", erro.message); return MOCK_BARBEIROS; }
  }
  return await chamarComRetry(() => API.listarBarbeiros());
}

async function carregarHorarios(data, barbeiroId) {
  const servicoId = state.servico ? state.servico.id : null; // slots dependem do serviço
  let horarios;
  if (EH_LOCAL) {
    try {
      horarios = (await API.listarHorariosDisponiveis(data, barbeiroId, servicoId)).horarios_disponiveis;
    } catch (erro) {
      console.warn("Backend offline (dev): usando mock.", erro.message);
      horarios = MOCK_HORARIOS;
    }
  } else {
    try {
      horarios = (await chamarComRetry(() => API.listarHorariosDisponiveis(data, barbeiroId, servicoId))).horarios_disponiveis;
    } catch (erro) {
      // Produção: sem mock. Degrada pra "sem horários" (nunca horário falso).
      horarios = [];
    }
  }

  // Filtra de novo aqui no front, como segunda camada de proteção
  // (ver explicação na seção "COOLDOWN" acima).
  return filtrarHorariosFuturos(horarios, data);
}


/* =====================================================
   CANCELAMENTO PELO CLIENTE (por telefone)
   Reaproveita os helpers do wizard (cabeçalho, voltar, carregando).
   ===================================================== */
function abrirCancelamento() {
  elStepper.style.display = "none";     // esconde a barra de passos
  renderCancelamento();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function voltarAoAgendamento() {
  elStepper.style.display = "";         // mostra a barra de passos de novo
  renderTudo();
}

function renderCancelamento() {
  elConteudo.innerHTML = "";
  elConteudo.appendChild(botaoVoltar(voltarAoAgendamento));
  elConteudo.insertAdjacentHTML("beforeend",
    cabecalhoPasso("Cancelar agendamento", "Informe seu telefone para ver e cancelar seus agendamentos"));

  const bloco = document.createElement("div");
  bloco.className = "bloco-cancelar";
  bloco.innerHTML = `
    <div class="campo-cancelar">
      <input type="tel" id="canc-telefone" class="campo-input" placeholder="Telefone com DDD (ex: 11999998888)" />
      <button class="btn btn-primario" id="canc-buscar">Buscar</button>
    </div>
    <p class="login-erro canc-erro" id="canc-erro"></p>
    <div id="canc-lista"></div>
  `;
  elConteudo.appendChild(bloco);

  const input = document.getElementById("canc-telefone");
  document.getElementById("canc-buscar").addEventListener("click", buscarAgendamentosCancelar);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") buscarAgendamentosCancelar(); });
  input.focus();
}

async function buscarAgendamentosCancelar() {
  const input = document.getElementById("canc-telefone");
  const erroEl = document.getElementById("canc-erro");
  const lista = document.getElementById("canc-lista");
  erroEl.textContent = "";
  const tel = input.value.trim();
  if (!/^\d{10,11}$/.test(tel.replace(/\D/g, ""))) {
    erroEl.textContent = "Digite um telefone válido com DDD.";
    return;
  }
  mostrarCarregando(lista, "Buscando seus agendamentos...");
  try {
    const ags = await API.consultarAgendamentos(tel);
    renderListaCancelar(ags, tel);
  } catch (erro) {
    lista.innerHTML = "";
    erroEl.textContent = erro.message || "Não foi possível buscar seus agendamentos.";
  }
}

function renderListaCancelar(ags, telefone) {
  const lista = document.getElementById("canc-lista");
  if (!ags.length) {
    lista.innerHTML = `<div class="canc-vazio">${ICONES.vazio}<span>Nenhum agendamento futuro encontrado para esse telefone.</span></div>`;
    return;
  }
  lista.innerHTML = ags.map((a) => `
    <div class="canc-item" data-item="${a.id}">
      <div class="canc-item-info">
        <strong>${formatarDataBR(a.data)} às ${a.hora.slice(0, 5)}</strong>
        <span>${a.servico_nome} · ${a.barbeiro_nome}</span>
      </div>
      <button class="btn-cancelar-item" data-cancelar="${a.id}">Cancelar</button>
    </div>
  `).join("");

  lista.querySelectorAll("[data-cancelar]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Cancelar este agendamento? Essa ação não pode ser desfeita.")) return;
      btn.disabled = true;
      btn.textContent = "...";
      try {
        await API.cancelarAgendamentoCliente(btn.dataset.cancelar, telefone);
        const item = btn.closest(".canc-item");
        item.classList.add("cancelado");
        btn.remove();
        item.querySelector(".canc-item-info").insertAdjacentHTML("beforeend",
          `<span class="canc-ok">Cancelado ✓</span>`);
      } catch (erro) {
        btn.disabled = false;
        btn.textContent = "Cancelar";
        alert(erro.message || "Não foi possível cancelar.");
      }
    });
  });
}

/* =====================================================
   START
   ===================================================== */
document.getElementById("link-cancelar").addEventListener("click", (e) => {
  e.preventDefault();
  abrirCancelamento();
});
renderTudo();
