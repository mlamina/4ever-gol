function getRandomColor() {
    const letters = '0123456789ABCDEF';
    let color = '#';
    for (let i = 0; i < 6; i++) {
        color += letters[Math.floor(Math.random() * 16)];
    }
    return color;
}

function getUserColor() {
    let userColor = localStorage.getItem('userColor');
    if (!userColor) {
        userColor = getRandomColor();
        localStorage.setItem('userColor', userColor);
    }
    return userColor;
}