import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { App } from "./App";
import "./index.css";
import { MandatePage } from "./MandatePage";
import { RefreshProvider } from "./refresh/RefreshProvider";
import { Shell } from "./Shell";
import { TreasuryPage } from "./TreasuryPage";
import { YieldsPage } from "./YieldsPage";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <RefreshProvider>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<App />} />
            <Route path="/mandate/:name" element={<MandatePage />} />
            <Route path="/treasury" element={<TreasuryPage />} />
            <Route path="/yields" element={<YieldsPage />} />
          </Route>
        </Routes>
      </RefreshProvider>
    </BrowserRouter>
  </StrictMode>,
);
