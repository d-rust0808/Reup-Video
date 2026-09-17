/** Packaged Electron talks to the bundled FastAPI process on this origin.
 *  Chromium blocks port 6000 (ERR_UNSAFE_PORT / X11), so the packaged
 *  backend binds 18790. Dev still uses Vite :6001 → proxy → :6000. */
export const DESKTOP_BACKEND_PORT = 18790;
export const DESKTOP_BACKEND_ORIGIN = `http://127.0.0.1:${DESKTOP_BACKEND_PORT}`;
export const DESKTOP_BACKEND_WS = `ws://127.0.0.1:${DESKTOP_BACKEND_PORT}`;
