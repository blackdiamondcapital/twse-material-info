let currentPage = 1;
const pageSize = 20;

const MARKET_LABELS = { TWSE: "上市", OTC: "上櫃" };

const els = {
  searchForm: document.getElementById("searchForm"),
  market: document.getElementById("market"),
  stockCode: document.getElementById("stockCode"),
  companyName: document.getElementById("companyName"),
  keyword: document.getElementById("keyword"),
  dateFrom: document.getElementById("dateFrom"),
  dateTo: document.getElementById("dateTo"),
  resetBtn: document.getElementById("resetBtn"),
  syncBtn: document.getElementById("syncBtn"),
  exportBtn: document.getElementById("exportBtn"),
  resultsList: document.getElementById("resultsList"),
  resultsInfo: document.getElementById("resultsInfo"),
  notifyStatus: document.getElementById("notifyStatus"),
  pagination: document.getElementById("pagination"),
  modal: document.getElementById("modal"),
  modalBody: document.getElementById("modalBody"),
  modalClose: document.getElementById("modalClose"),
  toast: document.getElementById("toast"),
  statTotal: document.getElementById("statTotal"),
  statTwse: document.getElementById("statTwse"),
  statOtc: document.getElementById("statOtc"),
  statLatestDate: document.getElementById("statLatestDate"),
};

function showToast(message, type = "info") {
  els.toast.textContent = message;
  els.toast.className = `toast ${type}`;
  setTimeout(() => els.toast.classList.add("hidden"), 4000);
}

function formatDate(iso) {
  if (!iso) return "-";
  const [y, m, d] = iso.split("-");
  return `${y}/${m}/${d}`;
}

function formatTime(t) {
  if (!t) return "";
  return t.slice(0, 8);
}

function buildQuery(page) {
  const params = new URLSearchParams();
  params.set("page", page);
  params.set("page_size", pageSize);

  if (els.market.value) params.set("market", els.market.value);
  if (els.stockCode.value.trim()) params.set("stock_code", els.stockCode.value.trim());
  if (els.companyName.value.trim()) params.set("company_name", els.companyName.value.trim());
  if (els.keyword.value.trim()) params.set("keyword", els.keyword.value.trim());
  if (els.dateFrom.value) params.set("date_from", els.dateFrom.value);
  if (els.dateTo.value) params.set("date_to", els.dateTo.value);

  return params.toString();
}

function buildFilterQuery() {
  const params = new URLSearchParams();
  if (els.market.value) params.set("market", els.market.value);
  if (els.stockCode.value.trim()) params.set("stock_code", els.stockCode.value.trim());
  if (els.companyName.value.trim()) params.set("company_name", els.companyName.value.trim());
  if (els.keyword.value.trim()) params.set("keyword", els.keyword.value.trim());
  if (els.dateFrom.value) params.set("date_from", els.dateFrom.value);
  if (els.dateTo.value) params.set("date_to", els.dateTo.value);
  return params.toString();
}

async function loadStats() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();
    els.statTotal.textContent = data.total.toLocaleString();
    els.statTwse.textContent = data.twse_count.toLocaleString();
    els.statOtc.textContent = data.otc_count.toLocaleString();
    els.statLatestDate.textContent = formatDate(data.latest_announce_date);

    const parts = [];
    if (data.notifications?.email_enabled) parts.push("Email");
    if (data.notifications?.telegram_enabled) parts.push("Telegram");
    const cronHint = data.platform === "vercel" ? " · Vercel Cron 每小時同步" : "";
    els.notifyStatus.textContent = parts.length
      ? `通知已啟用：${parts.join("、")}${cronHint}`
      : `自動同步已啟用${cronHint}`;
  } catch {
    showToast("無法載入統計資料", "error");
  }
}

async function loadAnnouncements(page = 1) {
  currentPage = page;
  els.resultsInfo.textContent = "載入中...";
  els.resultsList.innerHTML = "";

  try {
    const res = await fetch(`/api/announcements?${buildQuery(page)}`);
    const data = await res.json();

    if (data.total === 0) {
      els.resultsInfo.textContent = "查無資料";
      els.resultsList.innerHTML = '<div class="empty-state">目前沒有符合條件的重大訊息</div>';
      els.pagination.innerHTML = "";
      return;
    }

    const start = (data.page - 1) * data.page_size + 1;
    const end = Math.min(data.page * data.page_size, data.total);
    els.resultsInfo.textContent = `顯示 ${start}-${end} / 共 ${data.total.toLocaleString()} 筆`;

    els.resultsList.innerHTML = data.items
      .map(
        (item) => `
        <article class="announcement-card" data-id="${item.id}">
          <div class="card-meta">
            <span class="market-badge market-${item.market}">${MARKET_LABELS[item.market] || item.market}</span>
            <span class="stock-code">${item.stock_code}</span>
            <span>${item.company_name || ""}</span>
            <span>${formatDate(item.announce_date)} ${formatTime(item.announce_time)}</span>
            ${item.clause ? `<span>${item.clause}</span>` : ""}
          </div>
          <div class="card-subject">${escapeHtml(item.subject || "(無主旨)")}</div>
        </article>
      `
      )
      .join("");

    document.querySelectorAll(".announcement-card").forEach((card) => {
      card.addEventListener("click", () => openDetail(card.dataset.id));
    });

    renderPagination(data.page, data.total_pages);
  } catch {
    els.resultsInfo.textContent = "載入失敗";
    showToast("無法載入資料", "error");
  }
}

function renderPagination(page, totalPages) {
  if (totalPages <= 1) {
    els.pagination.innerHTML = "";
    return;
  }

  const buttons = [];
  buttons.push(
    `<button class="page-btn" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>&laquo;</button>`
  );

  getPageRange(page, totalPages).forEach((p) => {
    if (p === "...") {
      buttons.push('<span class="page-btn" disabled>...</span>');
    } else {
      buttons.push(
        `<button class="page-btn ${p === page ? "active" : ""}" data-page="${p}">${p}</button>`
      );
    }
  });

  buttons.push(
    `<button class="page-btn" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>&raquo;</button>`
  );

  els.pagination.innerHTML = buttons.join("");
  els.pagination.querySelectorAll(".page-btn[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = Number(btn.dataset.page);
      if (p >= 1 && p <= totalPages) loadAnnouncements(p);
    });
  });
}

function getPageRange(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = [1];
  if (current > 3) pages.push("...");
  for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
    pages.push(i);
  }
  if (current < total - 2) pages.push("...");
  pages.push(total);
  return pages;
}

async function openDetail(id) {
  try {
    const res = await fetch(`/api/announcements/${id}`);
    const item = await res.json();

    els.modalBody.innerHTML = `
      <h2 class="modal-title">${escapeHtml(item.subject || "(無主旨)")}</h2>
      <dl class="detail-grid">
        <dt>市場</dt><dd>${MARKET_LABELS[item.market] || item.market}</dd>
        <dt>公司代號</dt><dd>${item.stock_code}</dd>
        <dt>公司名稱</dt><dd>${escapeHtml(item.company_name || "-")}</dd>
        <dt>出表日期</dt><dd>${formatDate(item.report_date)}</dd>
        <dt>發言日期</dt><dd>${formatDate(item.announce_date)}</dd>
        <dt>發言時間</dt><dd>${formatTime(item.announce_time) || "-"}</dd>
        <dt>事實發生日</dt><dd>${formatDate(item.event_date)}</dd>
        <dt>符合條款</dt><dd>${escapeHtml(item.clause || "-")}</dd>
      </dl>
      <div class="detail-section">
        <h3>說明</h3>
        <pre>${escapeHtml(item.description || "無")}</pre>
      </div>
    `;
    els.modal.classList.remove("hidden");
  } catch {
    showToast("無法載入詳情", "error");
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function closeModal() {
  els.modal.classList.add("hidden");
}

async function triggerSync() {
  els.syncBtn.disabled = true;
  els.syncBtn.textContent = "同步中...";

  try {
    const res = await fetch("/api/sync", { method: "POST" });
    const data = await res.json();

    if (data.status === "success" || data.status === "partial") {
      showToast(
        `同步完成：上市 +${data.twse.inserted} / 上櫃 +${data.otc.inserted}`,
        data.status === "success" ? "success" : "info"
      );
      await loadStats();
      await loadAnnouncements(1);
    } else {
      showToast(`同步失敗：${data.message || "未知錯誤"}`, "error");
    }
  } catch {
    showToast("同步請求失敗", "error");
  } finally {
    els.syncBtn.disabled = false;
    els.syncBtn.textContent = "立即同步";
  }
}

function exportExcel() {
  const query = buildFilterQuery();
  window.location.href = `/api/announcements/export?${query}`;
}

els.searchForm.addEventListener("submit", (e) => {
  e.preventDefault();
  loadAnnouncements(1);
});

els.resetBtn.addEventListener("click", () => {
  els.searchForm.reset();
  loadAnnouncements(1);
});

els.syncBtn.addEventListener("click", triggerSync);
els.exportBtn.addEventListener("click", exportExcel);
els.modalClose.addEventListener("click", closeModal);
els.modal.querySelector(".modal-backdrop").addEventListener("click", closeModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

loadStats();
loadAnnouncements(1);
