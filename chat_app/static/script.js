document.addEventListener('DOMContentLoaded', () => {
    const userPhone = localStorage.getItem('userPhone');

    // Redirect if not logged in and on chat page
    if (!userPhone && window.location.pathname === '/chat') {
        window.location.href = '/';
        return;
    }

    if (window.location.pathname === '/chat') {
        initChat(userPhone);
    }
});

function initChat(userPhone) {
    document.getElementById('currentUser').textContent = userPhone;

    // Disconnect handler
    document.getElementById('disconnectBtn').addEventListener('click', () => {
        localStorage.removeItem('userPhone');
        window.location.href = '/';
    });

    const socket = io(); // Connects to the host that serves the page

    socket.emit('join', userPhone);

    const messagesArea = document.getElementById('messagesArea');
    const chatForm = document.getElementById('chatForm');
    const messageInput = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendBtn');
    const mediaInput = document.getElementById('mediaInput');
    const attachBtn = document.getElementById('attachBtn');
    const uploadProgressContainer = document.getElementById('uploadProgressContainer');
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');

    let isUploading = false;

    // Handle incoming messages
    socket.on('receive_message', (msg) => {
        const isMe = msg.sender === userPhone;

        const wrapper = document.createElement('div');
        wrapper.className = `message-wrapper ${isMe ? 'me' : 'other'}`;

        let html = '';
        if (!isMe) {
            html += `<span class="meta-text sender-name">${msg.sender}</span>`;
        }

        html += `<div class="message-bubble">`;

        if (msg.mediaUrl) {
            html += `<div class="media-container">`;
            if (msg.mediaType === 'image') {
                html += `<img src="${msg.mediaUrl}" alt="Attachment">`;
            } else if (msg.mediaType === 'video') {
                html += `<video src="${msg.mediaUrl}" controls></video>`;
            }
            html += `</div>`;
        }

        if (msg.text) {
            html += `<p>${msg.text}</p>`;
        }

        html += `</div>`;
        wrapper.innerHTML = html;

        messagesArea.appendChild(wrapper);
        scrollToBottom();
    });

    function scrollToBottom() {
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    // Handle sending message
    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        if (isUploading) return;

        const text = messageInput.value.trim();
        if (!text) return;

        socket.emit('send_message', {
            sender: userPhone,
            text: text
        });

        messageInput.value = '';
    });

    // Handle attach button click
    attachBtn.addEventListener('click', () => {
        mediaInput.click();
    });

    // Handle file selection
    mediaInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        // Check size (500MB limit)
        if (file.size > 500 * 1024 * 1024) {
            alert("File exceeds 500MB limit.");
            mediaInput.value = '';
            return;
        }

        uploadFile(file);
    });

    function uploadFile(file) {
        isUploading = true;
        sendBtn.disabled = true;
        attachBtn.disabled = true;
        uploadProgressContainer.classList.remove('hidden');
        progressBar.style.width = '0%';
        progressText.textContent = '0%';

        const formData = new FormData();
        formData.append('media', file);
        formData.append('sender', userPhone);

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/upload', true);

        xhr.upload.onprogress = (event) => {
            if (event.lengthComputable) {
                const percent = Math.round((event.loaded / event.total) * 100);
                progressBar.style.width = percent + '%';
                progressText.textContent = percent + '%';
            }
        };

        xhr.onload = () => {
            if (xhr.status === 200) {
                const res = JSON.parse(xhr.responseText);

                // If there's text in the input, send it with the media. 
                // Otherwise just send media.
                const text = messageInput.value.trim();

                socket.emit('send_message', {
                    sender: userPhone,
                    text: text,
                    mediaUrl: res.url,
                    mediaType: res.type
                });

                messageInput.value = '';
            } else {
                alert('Upload failed: ' + xhr.statusText);
            }
            cleanupUpload();
        };

        xhr.onerror = () => {
            alert('Upload failed due to network error.');
            cleanupUpload();
        };

        xhr.send(formData);
    }

    function cleanupUpload() {
        isUploading = false;
        sendBtn.disabled = false;
        attachBtn.disabled = false;
        uploadProgressContainer.classList.add('hidden');
        mediaInput.value = '';
    }
}
