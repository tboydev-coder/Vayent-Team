import React from "react";

import BrandLogo from "./BrandLogo";

const MobileUnsupportedPage: React.FC = () => {
  return (
    <main className="mobile-unsupported-shell" aria-labelledby="mobile-title">
      <section className="mobile-unsupported-panel">
        <BrandLogo className="mobile-unsupported-logo" />
        <p className="mobile-unsupported-kicker">Desktop required</p>
        <h1 id="mobile-title">Vayent is not accessible on mobile yet.</h1>
        <p>
          Please open Vayent on a computer or PC to use the database workspace.
        </p>
      </section>
    </main>
  );
};

export default MobileUnsupportedPage;
