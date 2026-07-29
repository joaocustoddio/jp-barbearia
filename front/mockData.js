/* =====================================================
   mockData.js
   Fallback usado APENAS quando o backend está offline.
   Quando o Flask está rodando, esses dados são ignorados.

   IMPORTANTE:
   - Preços/serviços reais → altere no banco de dados (tabela servicos)
   - Barbeiros reais       → altere no banco de dados (tabela barbeiros)
   - Horários reais        → altere no .env (HORARIO_ABERTURA etc)
   - Este arquivo só existe pra você visualizar o frontend
     sem precisar subir o Python toda hora durante o design.
   ===================================================== */

const MOCK_BARBEIROS = [
  { id: 1, nome: "Barbeiro 1" },
  { id: 2, nome: "Barbeiro 2" },
  { id: 3, nome: "Barbeiro 3" }
];

const MOCK_SERVICOS = [
  { id: 1, nome: "Degradê",       duracao_min: 40, preco: 35.0 },
  { id: 2, nome: "Social",        duracao_min: 30, preco: 25.0 },
  { id: 3, nome: "Navalhado",     duracao_min: 45, preco: 40.0 },
  { id: 4, nome: "Barba",         duracao_min: 20, preco: 20.0 },
  { id: 5, nome: "Corte + Barba", duracao_min: 60, preco: 50.0 },
  { id: 6, nome: "Sobrancelha",   duracao_min: 10, preco: 10.0 }
];

// Horários de exemplo (09:00-19:00, intervalos de 30min)
// Simula alguns já ocupados pra parecer mais real no mock
const MOCK_HORARIOS = [
  "09:00", "09:30",
  "10:30", "11:00", "11:30",
  "13:00", "13:30", "14:00",
  "15:00", "15:30", "16:00",
  "17:00", "17:30", "18:00", "18:30"
];
