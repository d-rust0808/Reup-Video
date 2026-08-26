/** Packaged Electron talks to the bundled FastAPI process on this origin. */
export const DESKTOP_BACKEND_PORT = 6000;
export const DESKTOP_BACKEND_ORIGIN = `http://127.0.0.1:${DESKTOP_BACKEND_PORT}`;
export const DESKTOP_BACKEND_WS = `ws://127.0.0.1:${DESKTOP_BACKEND_PORT}`;
