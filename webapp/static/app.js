/* Мита Mini App — без фреймворков, только DOM API. */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const initData = tg ? tg.initData : "";
  const STYLES = [
    ["brief", "Кратко"],
    ["detailed", "Подробно"],
    ["friendly", "Дружелюбно"],
    ["formal", "Формально"],
  ];

  const $ = (id) => document.getElementById(id);
  const state = { profile: null, style: "brief" };

  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#0a0a0a");
      tg.setBackgroundColor("#0a0a0a");
    } catch (e) {
      /* старые клиенты не поддерживают — не критично */
    }
  }

  function toast(message, isError) {
    const node = $("toast");
    node.textContent = message;
    node.classList.toggle("error", Boolean(isError));
    node.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => node.classList.remove("show"), 2600);
  }

  async function api(path, options) {
    const opts = Object.assign({ method: "GET" }, options || {});
    opts.headers = Object.assign(
      { "X-Init-Data": initData },
      opts.body ? { "Content-Type": "application/json" } : {}
    );
    const response = await fetch(path, opts);
    if (response.status === 204) return null;
    let payload = null;
    try {
      payload = await response.json();
    } catch (e) {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : `Ошибка ${response.status}`;
      throw new Error(detail);
    }
    return payload;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);
  }

  function formatDate(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }

  function emptyBlock(text) {
    return `<div class="empty">${escapeHtml(text)}</div>`;
  }

  /* ---------------- экраны ---------------- */

  async function loadNotes() {
    const list = $("notesList");
    const data = await api("/api/notes");
    if (!data.items.length) {
      list.innerHTML = emptyBlock("Заметок пока нет. Добавь первую выше.");
      return;
    }
    list.innerHTML = data.items.map((note) => `
      <div class="item ${note.done ? "done" : ""}" data-id="${note.id}">
        <button class="check ${note.done ? "on" : ""}" data-action="done" aria-label="Закрыть">✓</button>
        <div class="item-main">
          <div class="item-title">${escapeHtml(note.text)}</div>
          <div class="item-meta">#${note.id} · ${formatDate(note.created_at)}</div>
        </div>
        <button class="del" data-action="delete" aria-label="Удалить">✕</button>
      </div>`).join("");
  }

  async function loadReminders() {
    const list = $("remindersList");
    const data = await api("/api/reminders");
    if (!data.items.length) {
      list.innerHTML = emptyBlock("Напоминаний нет. Создай в боте: /remind завтра в 9:00 …");
      return;
    }
    const sorted = data.items.slice().sort((a, b) => a.remind_at.localeCompare(b.remind_at));
    list.innerHTML = sorted.map((item) => `
      <div class="item" data-id="${item.id}">
        <div class="item-main">
          <div class="item-title">${escapeHtml(item.text)}</div>
          <div class="item-meta">${formatDate(item.remind_at)}</div>
        </div>
        <button class="del" data-action="delete" aria-label="Удалить">✕</button>
      </div>`).join("");
  }

  async function loadMaterials() {
    const list = $("materialsList");
    const data = await api("/api/materials");
    if (!data.items.length) {
      list.innerHTML = emptyBlock("Материалов нет. Пришли документ, ссылку или текст в боте.");
      return;
    }
    list.innerHTML = data.items.map((item) => `
      <div class="item" data-id="${item.id}">
        <div class="item-main">
          <div class="item-title">${escapeHtml(item.title)}</div>
          <div class="item-meta">${escapeHtml(item.kind)} · ${item.chars} симв. · ${item.chunks} фрагм.</div>
        </div>
        <button class="del" data-action="delete" aria-label="Удалить">✕</button>
      </div>`).join("");
  }

  function renderStyle() {
    $("styleGrid").innerHTML = STYLES.map(([code, label]) => `
      <button class="style-btn ${code === state.style ? "active" : ""}" data-style="${code}">
        ${label}
      </button>`).join("");
  }

  function renderProfile() {
    const profile = state.profile;
    const card = $("profileCard");
    if (!profile) {
      card.innerHTML = emptyBlock("Профиль недоступен.");
      return;
    }
    card.innerHTML = `
      <div class="kv"><span>Цель</span><span>${escapeHtml(profile.goal || "—")}</span></div>
      <div class="kv"><span>Стиль</span><span>${escapeHtml(profile.style || "—")}</span></div>
      <div class="kv"><span>Часовой пояс</span><span>${escapeHtml(profile.timezone || "—")}</span></div>
      <div class="kv"><span>Сводка</span><span>${escapeHtml(profile.digest_time || "—")}</span></div>`;
  }

  async function loadAll() {
    try {
      const me = await api("/api/me");
      state.profile = me.profile;
      state.style = me.profile.style || "brief";
      $("userName").textContent = me.user.name;
      renderStyle();
      renderProfile();
    } catch (error) {
      toast(error.message, true);
      return;
    }
    await Promise.all([
      loadNotes().catch((e) => toast(e.message, true)),
      loadReminders().catch((e) => toast(e.message, true)),
      loadMaterials().catch((e) => toast(e.message, true)),
    ]);
  }

  /* ---------------- события ---------------- */

  function switchScreen(name) {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.screen === name);
    });
    document.querySelectorAll(".screen").forEach((screen) => {
      screen.classList.toggle("active", screen.id === `screen-${name}`);
    });
  }

  async function addQuickNote() {
    const input = $("quickInput");
    const text = input.value.trim();
    if (!text) {
      toast("Напиши текст заметки", true);
      return;
    }
    try {
      await api("/api/notes", { method: "POST", body: JSON.stringify({ text }) });
      input.value = "";
      toast("Заметка добавлена");
      await loadNotes();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function handleListClick(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const card = button.closest(".item");
    if (!card) return;
    const id = card.dataset.id;
    const action = button.dataset.action;
    const screen = card.closest(".screen").id;

    try {
      if (action === "done") {
        await api(`/api/notes/${id}/done`, { method: "POST" });
        await loadNotes();
      } else if (action === "delete") {
        const base = screen === "screen-notes" ? "notes"
          : screen === "screen-reminders" ? "reminders" : "materials";
        await api(`/api/${base}/${id}`, { method: "DELETE" });
        toast("Удалено");
        if (base === "notes") await loadNotes();
        else if (base === "reminders") await loadReminders();
        else await loadMaterials();
      }
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function handleStyleClick(event) {
    const button = event.target.closest("[data-style]");
    if (!button) return;
    const style = button.dataset.style;
    try {
      await api("/api/style", { method: "POST", body: JSON.stringify({ style }) });
      state.style = style;
      if (state.profile) state.profile.style = style;
      renderStyle();
      renderProfile();
      toast("Стиль обновлён");
    } catch (error) {
      toast(error.message, true);
    }
  }

  function init() {
    $("tabs").addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (tab) switchScreen(tab.dataset.screen);
    });
    $("quickAdd").addEventListener("click", addQuickNote);
    $("quickInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") addQuickNote();
    });
    $("refreshBtn").addEventListener("click", () => {
      loadAll().then(() => toast("Обновлено"));
    });
    ["notesList", "remindersList", "materialsList"].forEach((id) => {
      $(id).addEventListener("click", handleListClick);
    });
    $("styleGrid").addEventListener("click", handleStyleClick);

    if (!initData) {
      $("userName").textContent = "открой из Telegram";
      document.querySelectorAll(".list").forEach((list) => {
        list.innerHTML = emptyBlock("Открой это приложение через кнопку в боте.");
      });
      return;
    }
    loadAll();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
