/* =====================================================
   sw.js — Service Worker do JP Barbearia

   Objetivo: deixar o site instalável (vira app na tela do celular) e
   funcionar mesmo com internet ruim.

   Estratégia: REDE PRIMEIRO, cache como reserva.
   - Se a internet responder, usa sempre a versão mais nova (assim o `?v=`
     dos arquivos continua mandando e ninguém fica com tela velha).
   - Se a rede falhar, entrega o que estiver guardado — o app abre offline.
   - Chamadas da API (outro domínio) passam direto, nunca são guardadas:
     agenda e horários precisam ser sempre dados frescos.
   ===================================================== */

const CACHE = "jp-barbearia-v1";

// O essencial pra tela abrir mesmo sem internet.
const ARQUIVOS_BASE = [
  "./",
  "./index.html",
  "./style.css",
  "./api.js",
  "./script.js",
  "./manifest.json",
  "./img/icon-192.png",
];

self.addEventListener("install", (evento) => {
  // Guarda o básico e já assume o controle (sem esperar fechar as abas).
  evento.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(ARQUIVOS_BASE))
      .catch(() => { /* se algum arquivo falhar, instala mesmo assim */ })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (evento) => {
  // Limpa caches de versões antigas.
  evento.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(
        nomes.filter((nome) => nome !== CACHE).map((nome) => caches.delete(nome))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evento) => {
  const requisicao = evento.request;

  // Só cuida de GET do próprio site. API e terceiros passam direto.
  if (requisicao.method !== "GET") return;
  if (new URL(requisicao.url).origin !== self.location.origin) return;

  evento.respondWith(
    fetch(requisicao)
      .then((resposta) => {
        if (resposta && resposta.ok) {
          const copia = resposta.clone();
          caches.open(CACHE).then((cache) => cache.put(requisicao, copia));
        }
        return resposta;
      })
      .catch(() =>
        caches.match(requisicao).then((guardado) => guardado || caches.match("./index.html"))
      )
  );
});
