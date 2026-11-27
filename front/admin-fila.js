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
