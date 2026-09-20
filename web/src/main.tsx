import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { App } from "./App";
import "./index.css";
import { MandatePage } from "./MandatePage";
import { RefreshProvider } from "./refresh/RefreshProvider";
import { Shell } from "./Shell";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <RefreshProvider>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<App />} />
            <Route path="/mandate/:name" element={<MandatePage />} />
          </Route>
        </Routes>
      </RefreshProvider>
    </BrowserRouter>
  </StrictMode>,
);
