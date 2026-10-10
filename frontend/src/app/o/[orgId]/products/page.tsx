import { Suspense } from "react";
import { ProductsView } from "./ProductsView";

export default function ProductsPage() {
  return (
    <Suspense>
      <ProductsView />
    </Suspense>
  );
}
