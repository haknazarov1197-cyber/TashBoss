const tg = window.Telegram?.WebApp || null;
if (tg) tg.expand();

let balance = 0;

const industries = [
  { id: 1, name: "Уборка улиц", level: 1, income: 1, cost: 100 },
  { id: 2, name: "Коммунальные службы", level: 1, income: 3, cost: 300 },
  { id: 3, name: "Транспорт", level: 1, income: 8, cost: 1000 },
  { id: 4, name: "Парки", level: 1, income: 20, cost: 3000 },
  { id: 5, name: "Малый бизнес", level: 1, income: 50, cost: 8000 },
  { id: 6, name: "Заводы", level: 1, income: 120, cost: 20000 },
  { id: 7, name: "Качество воздуха", level: 1, income: 200, cost: 50000 },
  { id: 8, name: "IT-парк", level: 1, income: 500, cost: 120000 },
  { id: 9, name: "Туризм", level: 1, income: 1000, cost: 250000 },
  { id: 10, name: "Международное сотрудничество", level: 1, income: 5000, cost: 1000000 }
];

function render() {
  document.getElementById("balance").innerText = `💰 ${balance.toLocaleString()} BSS`;
  const container = document.getElementById("industries");
  container.innerHTML = "";

  industries.forEach(ind => {
    const div = document.createElement("div");
    div.className = "industry";
    div.innerHTML = `
      <h3>${ind.name} (ур. ${ind.level})</h3>
      <p>Прибыль: +${ind.income * ind.level} BSS</p>
      <button onclick="collect(${ind.id})">Собрать</button>
      <button onclick="upgrade(${ind.id})">Улучшить (${ind.cost.toLocaleString()} BSS)</button>
    `;
    container.appendChild(div);
  });
}

function collect(id) {
  const ind = industries.find(i => i.id === id);
  balance += ind.income * ind.level;
  save();
  render();
}

function upgrade(id) {
  const ind = industries.find(i => i.id === id);
  if (balance >= ind.cost) {
    balance -= ind.cost;
    ind.level++;
    ind.cost = Math.round(ind.cost * 1.3);
    save();
    render();
  } else {
    alert("Недостаточно BSS для улучшения!");
  }
}

// Сохранение
function save() {
  localStorage.setItem("tashboss_balance", balance);
  localStorage.setItem("tashboss_industries", JSON.stringify(industries));
}
function load() {
  balance = parseInt(localStorage.getItem("tashboss_balance")) || 0;
  const data = localStorage.getItem("tashboss_industries");
  if (data) {
    const saved = JSON.parse(data);
    for (let i = 0; i < industries.length; i++) {
      industries[i].level = saved[i]?.level || 1;
      industries[i].cost = saved[i]?.cost || industries[i].cost;
    }
  }
}

load();
render();
