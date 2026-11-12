/* =====================================================
   story.js — imagem dos horários livres pra postar no story

   Horário vago não se vende sozinho. Um story diário de "tem vaga hoje"
   transforma buraco de agenda em corte, e custa zero.

   A imagem é desenhada AQUI, no navegador, não no servidor. O backend roda no
   plano gratuito com 0,1 de CPU — gerar imagem lá deixaria o site lento pra
   quem está tentando agendar, que é exatamente quem não pode esperar.

   Base de cálculo: Degradê. Horário livre depende da duração do serviço, e um
   buraco de 30min cabe uma Barba mas não um Corte + Barba. Melhor o story
   prometer de menos e o site entregar de mais do que o contrário — "vejo no
   story e não consigo marcar" queima a confiança no agendamento inteiro.
   ===================================================== */
const STORY = {
  largura: 1080,
  altura: 1920,
  servicoBase: "Degradê",
  fundo: "#0a0c11",
  card: "#171a24",
  borda: "#333a4a",
  texto: "#f3f4f7",
  suave: "#9aa1b1",
  acento: "#f5a623",
};

/** Carrega a foto do barbeiro. Devolve null se não tiver ou falhar. */
function carregarFoto(arquivo) {
  return new Promise((resolve) => {
    if (!arquivo) return resolve(null);
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);     // sem foto o card desenha a inicial
    img.src = "img/" + arquivo;
  });
}

/** Junta nome, foto e horários livres de cada barbeiro ativo naquele dia. */
async function dadosDoStory(data) {
  const [servicos, barbeiros] = await Promise.all([
    API.listarServicos(data),
    API.listarBarbeiros(),
  ]);
  const base = servicos.find((s) => s.nome === STORY.servicoBase) || servicos[0];
  if (!base) throw new Error("Nenhum serviço cadastrado para calcular os horários.");

  return Promise.all(barbeiros.map(async (b) => {
    let horarios = [];
    try {
      const r = await API.listarHorariosDisponiveis(data, b.id, base.id);
      horarios = r.horarios_disponiveis || [];
    } catch (_) { /* barbeiro de folga ou erro pontual: entra sem horário */ }
    return { nome: b.nome, foto: await carregarFoto(b.foto), horarios };
  }));
}

/* ---------- desenho ---------- */

function cantoArredondado(ctx, x, y, l, a, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + l, y, x + l, y + a, r);
  ctx.arcTo(x + l, y + a, x, y + a, r);
  ctx.arcTo(x, y + a, x, y, r);
  ctx.arcTo(x, y, x + l, y, r);
  ctx.closePath();
}

function dataPorExtenso(iso) {
  const [a, m, d] = iso.split("-").map(Number);
  const dt = new Date(a, m - 1, d);
  const txt = dt.toLocaleDateString("pt-BR",
    { weekday: "long", day: "numeric", month: "long" });
  return txt.charAt(0).toUpperCase() + txt.slice(1);
}

function desenharStory(ctx, data, barbeiros) {
  const { largura: L, altura: A } = STORY;
  ctx.fillStyle = STORY.fundo;
  ctx.fillRect(0, 0, L, A);

  // Cabeçalho
  ctx.textAlign = "center";
  ctx.fillStyle = STORY.acento;
  ctx.font = "800 64px Sora, Arial, sans-serif";
  ctx.fillText("JP BARBEARIA", L / 2, 190);
  ctx.fillStyle = STORY.texto;
  ctx.font = "700 46px Inter, Arial, sans-serif";
  ctx.fillText("HORÁRIOS LIVRES", L / 2, 266);
  ctx.fillStyle = STORY.suave;
  ctx.font = "400 38px Inter, Arial, sans-serif";
  ctx.fillText(dataPorExtenso(data), L / 2, 330);

  // Cards: a altura de cada um cresce com a quantidade de horários, e o bloco
  // inteiro é centralizado — com 1 ou 3 barbeiros a imagem não fica torta.
  const margem = 70, larguraCard = L - margem * 2, porLinha = 4;

  // Quantos horários cabem em cada card. Em dia vazio um barbeiro pode ter 17
  // vagas; sem limite o card cresce e a imagem estoura os 1920px — justo no
  // dia em que mais interessa postar. O que não couber simplesmente não
  // aparece: quem abrir o site vai ver o resto.
  const alturaUtil = A - 420 - 230;                       // tira cabeçalho e rodapé
  const porCard = (alturaUtil - 34 * (barbeiros.length - 1)) / barbeiros.length;
  const maxLinhas = Math.max(1, Math.floor((porCard - 170) / 82));
  const maxChips = maxLinhas * porLinha;

  barbeiros = barbeiros.map((b) => ({ ...b, mostrar: b.horarios.slice(0, maxChips) }));

  const alturas = barbeiros.map((b) =>
    170 + Math.ceil(Math.max(b.mostrar.length, 1) / porLinha) * 82);
  const total = alturas.reduce((s, h) => s + h + 34, -34);
  let y = Math.max(420, (A - total) / 2 + 60);

  barbeiros.forEach((b, i) => {
    const h = alturas[i];
    ctx.fillStyle = STORY.card;
    cantoArredondado(ctx, margem, y, larguraCard, h, 28);
    ctx.fill();
    ctx.strokeStyle = STORY.borda;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Foto redonda (ou a inicial do nome quando não tem foto cadastrada)
    const fx = margem + 44, fy = y + 38, d = 96;
    ctx.save();
    ctx.beginPath();
    ctx.arc(fx + d / 2, fy + d / 2, d / 2, 0, Math.PI * 2);
    ctx.clip();
    if (b.foto) {
      const lado = Math.min(b.foto.width, b.foto.height);
      ctx.drawImage(b.foto, (b.foto.width - lado) / 2, (b.foto.height - lado) / 2,
                    lado, lado, fx, fy, d, d);
    } else {
      ctx.fillStyle = STORY.acento;
      ctx.fillRect(fx, fy, d, d);
      ctx.fillStyle = STORY.fundo;
      ctx.font = "800 52px Sora, Arial, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText((b.nome[0] || "?").toUpperCase(), fx + d / 2, fy + d / 2 + 18);
    }
    ctx.restore();

    ctx.textAlign = "left";
    ctx.fillStyle = STORY.texto;
    ctx.font = "700 44px Sora, Arial, sans-serif";
    ctx.fillText(b.nome.toUpperCase(), fx + d + 28, fy + 62);

    if (!b.mostrar.length) {
      ctx.fillStyle = STORY.suave;
      ctx.font = "400 36px Inter, Arial, sans-serif";
      ctx.fillText("Sem horário neste dia", margem + 44, y + 198);
    } else {
      const chipL = (larguraCard - 88 - 18 * (porLinha - 1)) / porLinha;
      b.mostrar.forEach((hora, k) => {
        const cx = margem + 44 + (k % porLinha) * (chipL + 18);
        const cy = y + 162 + Math.floor(k / porLinha) * 82;
        ctx.fillStyle = "rgba(245, 166, 35, 0.14)";
        cantoArredondado(ctx, cx, cy, chipL, 62, 16);
        ctx.fill();
        ctx.strokeStyle = STORY.acento;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.fillStyle = STORY.acento;
        ctx.font = "600 34px Inter, Arial, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(hora.slice(0, 5), cx + chipL / 2, cy + 41);
        ctx.textAlign = "left";
      });
    }
    y += h + 34;
  });

}

/** Gera a imagem do dia e abre o compartilhamento (ou baixa, no computador). */
async function gerarStory(data) {
  const barbeiros = await dadosDoStory(data);
  const canvas = document.createElement("canvas");
  canvas.width = STORY.largura;
  canvas.height = STORY.altura;
  desenharStory(canvas.getContext("2d"), data, barbeiros);

  const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
  const arquivo = new File([blob], `horarios-${data}.png`, { type: "image/png" });

  // No celular o menu do sistema deixa mandar direto pro Instagram. O download
  // puro cairia na pasta de arquivos e ele teria que ir procurar.
  if (navigator.canShare && navigator.canShare({ files: [arquivo] })) {
    try {
      await navigator.share({ files: [arquivo] });
      return "compartilhado";
    } catch (e) {
      // Fechou o menu de propósito: não baixa nada por cima.
      if (e && e.name === "AbortError") return "cancelado";
      // Qualquer outro motivo cai no download. O comum é o navegador recusar
      // por "gesto expirado": ele só abre o menu de compartilhar até ~5s depois
      // do clique, e buscar os horarios de todos os barbeiros leva mais que
      // isso. Antes esse caso virava um clique que não entregava nada.
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = arquivo.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return "baixado";
}
