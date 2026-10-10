import { Suspense } from "react";
import { RulesView } from "./RulesView";

export default function RulesPage() {
  return (
    <Suspense>
      <RulesView />
    </Suspense>
  );
}
