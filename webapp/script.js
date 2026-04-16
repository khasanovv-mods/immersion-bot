// В функции confirmOrder() замените:
function confirmOrder() {
    if (!selectedProduct) return;
    
    tg.HapticFeedback?.notificationOccurred('success');
    
    // Спрашиваем способ оплаты
    showPaymentMethodModal(selectedProduct);
}

function showPaymentMethodModal(product) {
    const modal = document.createElement('div');
    modal.className = 'modal show';
    modal.innerHTML = `
        <div class="modal-content">
            <h2>Выберите способ оплаты</h2>
            <p class="modal-desc">${product.name} — ${product.price} ₽</p>
            <div class="modal-buttons" style="flex-direction: column; gap: 10px;">
                <button class="btn-confirm" onclick="selectPaymentMethod('stars')">
                    ⭐ Telegram Stars
                </button>
                <button class="btn-confirm" onclick="selectPaymentMethod('yoomoney')">
                    💳 Банковская карта (ЮMoney)
                </button>
                <button class="btn-cancel" onclick="closePaymentModal()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

function selectPaymentMethod(method) {
    document.querySelectorAll('.modal').forEach(m => m.remove());
    
    tg.sendData(JSON.stringify({
        product_id: selectedProduct.id,
        product_name: selectedProduct.name,
        product_price: selectedProduct.price,
        payment_method: method
    }));
    
    tg.close();
}

function closePaymentModal() {
    document.querySelectorAll('.modal').forEach(m => m.remove());
}