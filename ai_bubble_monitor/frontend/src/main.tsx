import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Dashboard } from "./ui/Dashboard";
import "./ui/styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Dashboard />
  </StrictMode>,
);
