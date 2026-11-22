import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Dashboard } from "./ui/Dashboard";
import "./ui/styles.css";
import aurisMark from "./ui/auris-mark.svg?url";

const favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]') ?? document.createElement("link");
favicon.rel = "icon";
favicon.type = "image/svg+xml";
favicon.href = aurisMark;
if (!favicon.isConnected) document.head.append(favicon);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Dashboard />
  </StrictMode>,
);
