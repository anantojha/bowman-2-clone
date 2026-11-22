// Where the game server lives. Leave `server` empty when the same host serves this page and the
// game (the Docker image and `uvicorn` do). For the GitHub Pages copy, the deploy workflow
// overwrites this file with the server's wss:// address, for example
//   wss://bowman-2-server.onrender.com/ws
window.BOWMAN_CONFIG = {
  server: "",
};
