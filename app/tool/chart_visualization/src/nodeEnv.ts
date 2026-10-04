/**
 * Node.js >= 21 defines a global `navigator`. Browser-oriented dependencies of VMind
 * (lottie-web via VChart) use it to detect a DOM and then crash on `document` at
 * import time, so it is removed before those modules load. Import this module first.
 */
delete (globalThis as { navigator?: unknown }).navigator;

export {};
