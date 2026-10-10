import { Suspense } from "react";
import { DocumentsView } from "./DocumentsView";

export default function DocumentsPage() {
  return (
    <Suspense>
      <DocumentsView />
    </Suspense>
  );
}
