/* =====================================================
   api.js
   Isola TODA comunicação com o backend (fetch).
   Se a URL da API mudar (ex: indo pra produção), só
   precisa trocar BASE_URL aqui — nada mais no projeto
   precisa saber o endereço real do servidor.

   Cada função retorna uma Promise que resolve com os
   dados já tratados, ou lança um erro que o script.js
   trata (ex: mostrando mensagem de erro no chat).
   ===================================================== */

const API = (() => {

  // URL do backend: detecta automaticamente ambiente.
  // - Local (abrindo em localhost/127.0.0.1): usa o Flask local na porta 5000.
  // - Produção (Vercel): usa o backend hospedado no Render.
  // >>> Depois de criar o Web Service no Render, troque a URL de produção abaixo. <<<
  const URL_PRODUCAO = "https://jp-barbearia-r9lq.onrender.com";
  const ehLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
  const BASE_URL = ehLocal ? "http://127.0.0.1:5000" : URL_PRODUCAO;

  const CHAVE_TOKEN = "admin_token"; // onde o JWT do admin fica guardado

  function getToken()  { return localStorage.getItem(CHAVE_TOKEN); }
  function setToken(t)  { localStorage.setItem(CHAVE_TOKEN, t); }
  function limparToken() { localStorage.removeItem(CHAVE_TOKEN); }

  /** Cabeçalho de autorização com o token do admin (vazio se não logado). */
  function authHeaders() {
    const t = getToken();
    return t ? { "Authorization": `Bearer ${t}` } : {};
  }

  /**
   * Função genérica de requisição.
   * Centraliza tratamento de erro para não repetir try/catch
   * em cada função abaixo.
   */
  async function request(path, options = {}) {
    let resposta;
    try {
      resposta = await fetch(`${BASE_URL}${path}`, {
        ...options,
        // headers do options são mesclados (não substituem o Content-Type)
        headers: { "Content-Type": "application/json", ...(options.headers || {}) }
      });
    } catch (erroDeRede) {
      // Acontece quando o backend está offline, ou CORS bloqueado, etc.
      throw new Error("Não foi possível conectar ao servidor. Verifique se o backend está rodando.");
    }

    const dados = await resposta.json().catch(() => null);

    if (!resposta.ok) {
      // 401 = token ausente/expirado: sinaliza pra tela de admin voltar pro login
      if (resposta.status === 401) {
        const erro = new Error(dados?.erro || "Sessão expirada. Faça login novamente.");
        erro.naoAutorizado = true;
        throw erro;
      }
      // O backend manda { erro: "mensagem" } nos casos de erro (ver app.py)
      const mensagem = dados?.erro || "Ocorreu um erro inesperado.";
      throw new Error(mensagem);
    }

    return dados;
  }

  /** Atalho pra requisições autenticadas (injeta o Bearer token). */
  function requestAuth(path, options = {}) {
    return request(path, {
      ...options,
      headers: { ...authHeaders(), ...(options.headers || {}) }
    });
  }

  return {
    /** GET /api/servicos */
    listarServicos() {
      return request("/api/servicos");
    },

    /** GET /api/barbeiros */
    listarBarbeiros() {
      return request("/api/barbeiros");
    },

    /** GET /api/horarios-disponiveis?data=&barbeiro_id=&servico_id= (slots pela duração do serviço) */
    listarHorariosDisponiveis(data, barbeiroId, servicoId) {
      const s = servicoId != null ? `&servico_id=${encodeURIComponent(servicoId)}` : "";
      return request(`/api/horarios-disponiveis?data=${encodeURIComponent(data)}&barbeiro_id=${barbeiroId}${s}`);
    },

    /**
     * POST /api/agendamentos
     * dadosAgendamento = { nome_cliente, telefone, servico_id, data, hora }
     */
    criarAgendamento(dadosAgendamento) {
      return request("/api/agendamentos", {
        method: "POST",
        body: JSON.stringify(dadosAgendamento)
      });
    },

    /** GET /api/agendamentos/consultar?telefone= → agendamentos futuros do cliente */
    consultarAgendamentos(telefone) {
      return request(`/api/agendamentos/consultar?telefone=${encodeURIComponent(telefone)}`);
    },

    /** POST /api/agendamentos/cancelar → cliente cancela o próprio (confere telefone) */
    cancelarAgendamentoCliente(agendamento_id, telefone) {
      return request("/api/agendamentos/cancelar", {
        method: "POST",
        body: JSON.stringify({ agendamento_id, telefone })
      });
    },

    /* =====================================================
       ÁREA ADMINISTRATIVA (painel do barbeiro)
       Todas as rotas abaixo exigem token JWT.
       ===================================================== */
    admin: {
      estaLogado() { return !!getToken(); },

      logout() {
        limparToken();
        localStorage.removeItem("admin_papel");
        localStorage.removeItem("admin_barbeiro_nome");
        localStorage.removeItem("admin_barbeiro_id");
      },

      /** POST /api/admin/login → guarda token + papel/barbeiro e devolve os dados. */
      async login(usuario, senha) {
        const dados = await request("/api/admin/login", {
          method: "POST",
          body: JSON.stringify({ usuario, senha })
        });
        if (dados?.token) {
          setToken(dados.token);
          localStorage.setItem("admin_papel", dados.papel || "");
          localStorage.setItem("admin_barbeiro_nome", dados.barbeiro_nome || "");
          localStorage.setItem("admin_barbeiro_id", dados.barbeiro_id ?? "");
        }
        return dados;
      },

      /** Papel do usuário logado ('master' | 'barbeiro') e atalhos. */
      papel() { return localStorage.getItem("admin_papel") || ""; },
      ehMaster() { return this.papel() === "master"; },
      podeVerValores() { return this.papel() !== "salao"; },
      barbeiroNome() { return localStorage.getItem("admin_barbeiro_nome") || ""; },
      barbeiroId() {
        const v = localStorage.getItem("admin_barbeiro_id");
        return v ? Number(v) : null;
      },

      /** GET /api/admin/agendamentos?data=&status= */
      listarAgendamentos({ data, status } = {}) {
        const params = new URLSearchParams();
        if (data) params.set("data", data);
        if (status) params.set("status", status);
        const qs = params.toString();
        return requestAuth(`/api/admin/agendamentos${qs ? "?" + qs : ""}`);
      },

      /** PATCH /api/admin/agendamentos/:id/cancelar */
      cancelarAgendamento(id) {
        return requestAuth(`/api/admin/agendamentos/${id}/cancelar`, { method: "PATCH" });
      },

      /** POST /api/admin/agendamentos → registra agendamento pelo painel (walk-in) */
      criarAgendamento(dados) {
        return requestAuth("/api/admin/agendamentos", {
          method: "POST",
          body: JSON.stringify(dados)
        });
      },

      /** GET /api/admin/barbeiros → todos (inclui inativos) */
      listarBarbeiros() {
        return requestAuth("/api/admin/barbeiros");
      },

      /** PATCH /api/admin/barbeiros/:id → inativar/reativar */
      definirBarbeiroAtivo(id, ativo) {
        return requestAuth(`/api/admin/barbeiros/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ ativo })
        });
      },

      /** GET /api/admin/relatorio?periodo=dia|semana|mes */
      relatorio(periodo = "dia") {
        return requestAuth(`/api/admin/relatorio?periodo=${encodeURIComponent(periodo)}`);
      },

      /** GET /api/admin/bloqueios */
      listarBloqueios() {
        return requestAuth("/api/admin/bloqueios");
      },

      /** POST /api/admin/bloqueios → { data, hora?, motivo? } */
      criarBloqueio(dados) {
        return requestAuth("/api/admin/bloqueios", {
          method: "POST",
          body: JSON.stringify(dados)
        });
      },

      /** DELETE /api/admin/bloqueios/:id */
      removerBloqueio(id) {
        return requestAuth(`/api/admin/bloqueios/${id}`, { method: "DELETE" });
      },

      /** GET /api/admin/contagem?data= → fechamento do dia por barbeiro (comissão) */
      contagem(data) {
        const qs = data ? `?data=${encodeURIComponent(data)}` : "";
        return requestAuth(`/api/admin/contagem${qs}`);
      },

      /** PUT /api/admin/barbeiros/:id/login → master cria/edita login do barbeiro */
      definirLoginBarbeiro(id, usuario, senha) {
        return requestAuth(`/api/admin/barbeiros/${id}/login`, {
          method: "PUT",
          body: JSON.stringify({ usuario, senha })
        });
      },

      /** GET /api/admin/almoco?data= → almoço do barbeiro no dia (ou null) */
      obterAlmoco(data) {
        return requestAuth(`/api/admin/almoco?data=${encodeURIComponent(data)}`);
      },

      /** POST /api/admin/almoco → bloqueia 60min de almoço { data, hora } */
      marcarAlmoco(data, hora) {
        return requestAuth("/api/admin/almoco", {
          method: "POST",
          body: JSON.stringify({ data, hora })
        });
      },

      /** DELETE /api/admin/almoco?data= → libera o almoço do barbeiro no dia */
      liberarAlmoco(data) {
        return requestAuth(`/api/admin/almoco?data=${encodeURIComponent(data)}`, {
          method: "DELETE"
        });
      },

      /** GET /api/admin/almoco-fixo → almoço fixo (todo dia) do barbeiro */
      obterAlmocoFixo() {
        return requestAuth("/api/admin/almoco-fixo");
      },

      /** PUT /api/admin/almoco-fixo → define almoço fixo { hora } */
      definirAlmocoFixo(hora) {
        return requestAuth("/api/admin/almoco-fixo", {
          method: "PUT",
          body: JSON.stringify({ hora })
        });
      },

      /** DELETE /api/admin/almoco-fixo → remove o almoço fixo */
      removerAlmocoFixo() {
        return requestAuth("/api/admin/almoco-fixo", { method: "DELETE" });
      },

      /** GET /api/admin/acessos → lista todos os logins (master) */
      listarAcessos() {
        return requestAuth("/api/admin/acessos");
      },

      /** PUT /api/admin/senha → master troca a senha de um login { usuario, senha } */
      trocarSenha(usuario, senha) {
        return requestAuth("/api/admin/senha", {
          method: "PUT",
          body: JSON.stringify({ usuario, senha })
        });
      },

      /** PUT /api/admin/minha-senha → usuário logado troca a própria senha */
      trocarMinhaSenha(senha_atual, nova_senha) {
        return requestAuth("/api/admin/minha-senha", {
          method: "PUT",
          body: JSON.stringify({ senha_atual, nova_senha })
        });
      }
    }
  };

})();
