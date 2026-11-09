/* =====================================================
   medicao.js — Meta Pixel do site de agendamento

   Por que um arquivo só pra isso: o site é UMA página. Os cinco passos
   (serviço, profissional, data, horário, dados) trocam por JavaScript, sem
   mudar de URL. O código padrão do Pixel dispara PageView uma vez e para —
   o Meta veria "entrou no site" e mais nada, e a campanha só poderia ser
   otimizada por CLIQUE. Aqui cada passo vira um evento.

   NÃO entra no painel do barbeiro (admin.html): ele usa o sistema o dia
   inteiro e viraria "tráfego", envenenando os públicos da campanha.

   COMO LIGAR: ponha o ID do Pixel abaixo. Com o campo vazio, nada é
   carregado e o site funciona igual — então dá pra subir antes de ter conta.
   ===================================================== */
const PIXEL_ID = "";

/* Eventos: usamos os nomes PADRÃO do Meta sempre que existe um que descreve
   o passo. Evento padrão o gerenciador já entende e deixa escolher como
   objetivo de campanha; evento inventado vira só um número solto.

   Schedule é o evento de conversão — feito exatamente pra agendamento. Vai
   com value e currency pra a campanha poder otimizar por VALOR: assim o Meta
   aprende a buscar quem marca Corte + Barba, não só quem marca Barba. */
(function iniciarPixel() {
  if (!PIXEL_ID) return;
  /* eslint-disable */
  !function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
  n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
  n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
  t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}
  (window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
  /* eslint-enable */
  fbq("init", PIXEL_ID);
  fbq("track", "PageView");
})();

/** Dispara um evento, se o pixel estiver ligado. Fora isso, não faz nada. */
window.pixel = function (evento, dados) {
  if (!PIXEL_ID || typeof fbq !== "function") return;
  try {
    fbq("track", evento, dados || {});
  } catch (_) {
    /* bloqueador derrubou o fbevents: o agendamento não pode cair junto */
  }
};
