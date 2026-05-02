const searchInput = document.getElementById("searchInput");
const users = document.querySelectorAll(".user");
const userList = document.getElementById("userList");
const appContainer = document.querySelector(".container");
const chatBox = document.querySelector(".chat-box");
const form = document.querySelector(".chat-input");
const input = form ? form.querySelector("input[name='message']") : null;
const currentUserId = Number.parseInt(
    (appContainer && appContainer.dataset.currentUserId) || window.currentUserId,
    10
);
window.currentUserId = Number.isNaN(currentUserId) ? null : currentUserId;
let socket = null;
let pollTimer = null;
let pollInFlight = false;
let optimisticCounter = 0;

if (searchInput) {
    searchInput.addEventListener("keyup", () => {
        const value = searchInput.value.toLowerCase();
        users.forEach((user) => {
            const name = user.textContent.toLowerCase();
            user.style.display = name.includes(value) ? "block" : "none";
        });
    });
}

if (chatBox) {
    chatBox.scrollTop = chatBox.scrollHeight;
}

function getCurrentChatId() {
    const params = new URLSearchParams(window.location.search);
    const chatId = parseInt(params.get("chat"), 10);
    return Number.isNaN(chatId) ? null : chatId;
}

function normalizeMessageData(data) {
    const messageId = Number.parseInt(data.id, 10);
    return {
        ...data,
        id: Number.isNaN(messageId) ? data.id : messageId,
        sender_id: Number.parseInt(data.sender_id, 10),
        receiver_id: Number.parseInt(data.receiver_id, 10)
    };
}

function showMessageError(message) {
    const existing = document.querySelector(".message-error");
    if (existing) {
        existing.remove();
    }

    const error = document.createElement("p");
    error.className = "message-error";
    error.textContent = message;

    if (form) {
        form.insertAdjacentElement("beforebegin", error);
    }

    window.setTimeout(() => {
        error.remove();
    }, 3000);
}

function buildMessageRow(data) {
    const isMine = data.sender_id === window.currentUserId;
    const row = document.createElement("div");
    row.className = isMine ? "message-row mine" : "message-row other";
    row.setAttribute("data-id", data.id);
    if (data.client_nonce) {
        row.setAttribute("data-client-nonce", data.client_nonce);
    }

    const bubble = document.createElement("article");
    bubble.className = isMine ? "message-bubble my-msg" : "message-bubble other-msg";
    bubble.setAttribute("tabindex", "0");

    const text = document.createElement("p");
    text.className = "message-text";
    text.textContent = data.message;

    bubble.appendChild(text);
    if (isMine) {
        const status = document.createElement("small");
        status.className = "message-status";
        status.textContent = data.status || "sent";
        bubble.appendChild(status);
    }
    row.appendChild(bubble);

    const actions = document.createElement("div");
    actions.className = "message-actions-wrap";
    row.appendChild(actions);

    return row;
}

function getConversationLink(userId) {
    return document.querySelector(`.user[data-user-id="${userId}"]`);
}

function updateConversationPreview(userId, message, unreadDelta, moveToTop) {
    const link = getConversationLink(userId);
    if (!link) {
        return;
    }

    const preview = link.querySelector(".user-last-message");
    if (preview) {
        preview.textContent = message || "";
    }

    const badge = link.querySelector(".badge");
    if (badge) {
        const currentValue = parseInt((badge.textContent || "0").trim(), 10);
        const nextValue = Math.max(0, (Number.isNaN(currentValue) ? 0 : currentValue) + unreadDelta);
        badge.textContent = nextValue > 0 ? String(nextValue) : "";
        badge.classList.toggle("hidden", nextValue === 0);
    }

    if (moveToTop && userList && link.parentElement === userList) {
        userList.prepend(link);
    }
}

function clearConversationUnread(userId) {
    const link = getConversationLink(userId);
    if (!link) {
        return;
    }
    const badge = link.querySelector(".badge");
    if (badge) {
        badge.textContent = "";
        badge.classList.add("hidden");
    }
}

function updateMessageStatus(messageId, status) {
    const row = document.querySelector(`[data-id="${messageId}"]`);
    if (!row || !row.classList.contains("mine")) {
        return;
    }
    const statusNode = row.querySelector(".message-status");
    if (statusNode) {
        statusNode.textContent = status;
    }
}

function removeMessageRowByNonce(clientNonce) {
    if (!clientNonce) {
        return;
    }
    const row = document.querySelector(`[data-client-nonce="${clientNonce}"]`);
    if (row) {
        row.remove();
    }
}

function reconcileOptimisticMessage(clientNonce, rawData) {
    const data = normalizeMessageData(rawData);
    if (!clientNonce) {
        renderMessage(data);
        return;
    }

    const row = document.querySelector(`[data-client-nonce="${clientNonce}"]`);
    if (!row) {
        renderMessage(data);
        return;
    }

    row.setAttribute("data-id", data.id);
    row.removeAttribute("data-client-nonce");

    const textNode = row.querySelector(".message-text");
    if (textNode) {
        textNode.textContent = data.message;
    }

    const statusNode = row.querySelector(".message-status");
    if (statusNode) {
        statusNode.textContent = data.status || "sent";
    }
}

function renderMessage(rawData) {
    const data = normalizeMessageData(rawData);
    const chatId = getCurrentChatId();
    if (!chatId || !chatBox) {
        return;
    }

    const currentUser = window.currentUserId;
    const belongsToChat = (
        (data.sender_id === currentUser && data.receiver_id === chatId) ||
        (data.sender_id === chatId && data.receiver_id === currentUser)
    );
    if (!belongsToChat) {
        return;
    }

    const existingByNonce = data.client_nonce
        ? document.querySelector(`[data-client-nonce="${data.client_nonce}"]`)
        : null;
    if (existingByNonce) {
        existingByNonce.setAttribute("data-id", data.id);
        if (data.sender_id === currentUser) {
            const statusNode = existingByNonce.querySelector(".message-status");
            if (statusNode) {
                statusNode.textContent = data.status || "sent";
            }
        }
        existingByNonce.removeAttribute("data-client-nonce");
        return;
    }

    const existing = document.querySelector(`[data-id="${data.id}"]`);
    if (existing) {
        if (data.sender_id === currentUser) {
            updateMessageStatus(data.id, data.status || "sent");
        }
        return;
    }

    chatBox.appendChild(buildMessageRow(data));
    chatBox.scrollTop = chatBox.scrollHeight;
}

function emitOpenChat() {
    if (!socket || !socket.connected) {
        return;
    }
    const chatId = getCurrentChatId();
    if (!chatId) {
        return;
    }
    clearConversationUnread(chatId);
    socket.emit("open_chat", { chat_user_id: chatId });
}

function getLastMessageId() {
    if (!chatBox) {
        return 0;
    }
    const rows = Array.from(chatBox.querySelectorAll("[data-id]"));
    for (let index = rows.length - 1; index >= 0; index -= 1) {
        const value = parseInt(rows[index].getAttribute("data-id"), 10);
        if (!Number.isNaN(value)) {
            return value;
        }
    }
    return 0;
}

async function pollForMessages() {
    const chatId = getCurrentChatId();
    if (!chatId || pollInFlight || document.visibilityState === "hidden") {
        return;
    }

    const lastMessageId = getLastMessageId();
    const afterId = Math.max(0, lastMessageId);
    const requestUrl = `/api/chat/${chatId}/messages?after_id=${afterId}&_ts=${Date.now()}`;

    pollInFlight = true;
    try {
        const response = await fetch(requestUrl, {
            headers: {
                "Accept": "application/json"
            },
            credentials: "same-origin"
        });
        if (response.status === 401) {
            stopFallbackPolling();
            return;
        }
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        if (!payload.ok || !Array.isArray(payload.messages)) {
            return;
        }
        payload.messages.forEach((rawMessage) => {
            const message = normalizeMessageData(rawMessage);
            renderMessage(message);
            const otherUserId = message.sender_id === window.currentUserId ? message.receiver_id : message.sender_id;
            const isUnreadIncoming = message.receiver_id === window.currentUserId && message.sender_id !== getCurrentChatId();
            updateConversationPreview(otherUserId, message.message, isUnreadIncoming ? 1 : 0, true);
        });
    } catch (error) {
        // Ignore and retry on the next interval.
    } finally {
        pollInFlight = false;
    }
}

function startFallbackPolling() {
    if (pollTimer || !chatBox) {
        return;
    }
    pollForMessages();
    pollTimer = window.setInterval(pollForMessages, 1200);
}

function stopFallbackPolling() {
    if (!pollTimer) {
        return;
    }
    window.clearInterval(pollTimer);
    pollTimer = null;
}

function buildClientNonce() {
    optimisticCounter += 1;
    return `local-${window.currentUserId}-${Date.now()}-${optimisticCounter}`;
}

async function fallbackSendMessage(message, clientNonce) {
    const formData = new FormData(form);
    formData.set("message", message);
    formData.set("client_nonce", clientNonce);

    const response = await fetch(form.action, {
        method: "POST",
        body: formData,
        headers: {
            "Accept": "application/json"
        },
        credentials: "same-origin"
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
        ? await response.json()
        : { ok: false, error: "Please reload this page and log in again." };
    if (!response.ok || !payload.ok) {
        throw new Error((payload && payload.error) || "Message was not sent.");
    }
    return payload.message;
}

if (form && input) {
    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const message = input.value.trim();
        if (!message) {
            return;
        }

        const chatId = getCurrentChatId();
        if (!chatId) {
            return;
        }

        const submitButton = form.querySelector("button[type='submit']");
        if (submitButton) {
            submitButton.disabled = true;
        }

        let clientNonce = null;
        try {
            clientNonce = buildClientNonce();
            renderMessage({
                id: `temp-${clientNonce}`,
                sender_id: window.currentUserId,
                receiver_id: chatId,
                message: message,
                status: "sending",
                client_nonce: clientNonce
            });
            input.value = "";

            const savedMessage = await fallbackSendMessage(message, clientNonce);
            reconcileOptimisticMessage(clientNonce, savedMessage);
            updateConversationPreview(chatId, message, 0, true);
        } catch (error) {
            removeMessageRowByNonce(clientNonce);
            input.value = message;
            showMessageError(error.message || "Message was not sent.");
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
            }
            input.focus();
        }
    });
}

if (typeof io !== "undefined") {
    try {
        socket = io({
            reconnection: true,
            reconnectionAttempts: Infinity,
            reconnectionDelay: 250,
            reconnectionDelayMax: 1500,
            transports: ["polling", "websocket"]
        });

        socket.on("connect", () => {
            emitOpenChat();
        });

        socket.on("disconnect", () => {
            startFallbackPolling();
        });

        socket.on("connect_error", () => {
            startFallbackPolling();
        });

        socket.on("new_message", (rawData) => {
            const data = normalizeMessageData(rawData);
            if (data.sender_id === window.currentUserId && data.client_nonce) {
                reconcileOptimisticMessage(data.client_nonce, data);
            } else {
                renderMessage(data);
            }

            const chatId = getCurrentChatId();
            const otherUserId = data.sender_id === window.currentUserId ? data.receiver_id : data.sender_id;
            const isActiveChat = chatId === otherUserId;
            const unreadDelta = data.receiver_id === window.currentUserId && !isActiveChat ? 1 : 0;
            updateConversationPreview(otherUserId, data.message, unreadDelta, true);

            if (data.sender_id === window.currentUserId) {
                return;
            }

            if (!chatId || data.receiver_id !== window.currentUserId) {
                return;
            }

            const status = data.sender_id === chatId ? "seen" : "delivered";
            socket.emit("message_received", {
                message_id: data.id,
                status: status
            });
        });

        socket.on("message_status", (data) => {
            const messageIds = Array.isArray(data.message_ids) ? data.message_ids : [];
            messageIds.forEach((messageId) => {
                updateMessageStatus(messageId, data.status);
            });
        });
    } catch (error) {
        socket = null;
        startFallbackPolling();
    }
}
else {
    startFallbackPolling();
}

startFallbackPolling();

const actionWraps = document.querySelectorAll(".message-actions-wrap");

function closeAllMessageMenus() {
    actionWraps.forEach((wrap) => {
        wrap.classList.remove("open");
        const trigger = wrap.querySelector(".message-menu-trigger");
        const editForm = wrap.querySelector(".edit-form");
        if (trigger) {
            trigger.setAttribute("aria-expanded", "false");
        }
        if (editForm) {
            editForm.classList.remove("show");
        }
    });
}

function showCopyFeedback() {
    const existing = document.querySelector(".copy-feedback");
    if (existing) {
        existing.remove();
    }
    const toast = document.createElement("div");
    toast.className = "copy-feedback";
    toast.textContent = "Message copied";
    document.body.appendChild(toast);
    window.setTimeout(() => {
        toast.remove();
    }, 1400);
}

async function copyMessageText(text) {
    try {
        await navigator.clipboard.writeText(text);
        showCopyFeedback();
    } catch (error) {
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "absolute";
        area.style.left = "-9999px";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        showCopyFeedback();
    }
}

if (actionWraps.length > 0) {
    actionWraps.forEach((wrap) => {
        const trigger = wrap.querySelector(".message-menu-trigger");
        const copyBtn = wrap.querySelector(".copy-message");

        if (trigger) {
            trigger.addEventListener("click", (event) => {
                event.stopPropagation();
                const wasOpen = wrap.classList.contains("open");
                closeAllMessageMenus();
                if (!wasOpen) {
                    wrap.classList.add("open");
                    trigger.setAttribute("aria-expanded", "true");
                }
            });
        }

        if (copyBtn) {
            copyBtn.addEventListener("click", async () => {
                const text = copyBtn.getAttribute("data-message-text") || "";
                await copyMessageText(text);
                closeAllMessageMenus();
            });
        }
    });

    document.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        if (!target.closest(".message-actions-wrap")) {
            closeAllMessageMenus();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeAllMessageMenus();
        }
    });
}

window.addEventListener("focus", () => {
    emitOpenChat();
    if (!socket || !socket.connected) {
        pollForMessages();
    }
});

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
        emitOpenChat();
        if (!socket || !socket.connected) {
            pollForMessages();
        }
    }
});

window.addEventListener("pageshow", () => {
    emitOpenChat();
    if (!socket || !socket.connected) {
        pollForMessages();
    }
});
