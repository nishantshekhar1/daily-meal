import { Routes, Route } from "react-router-dom";
import { BottomNav } from "@/components/layout/BottomNav";
import PantryPage from "@/pages/PantryPage";
import MealsPage from "@/pages/MealsPage";
import ReceiptsPage from "@/pages/ReceiptsPage";
import HouseholdPage from "@/pages/HouseholdPage";

export default function App() {
  return (
    <div className="min-h-screen bg-background">
      <Routes>
        <Route path="/" element={<PantryPage />} />
        <Route path="/meals" element={<MealsPage />} />
        <Route path="/receipts" element={<ReceiptsPage />} />
        <Route path="/household" element={<HouseholdPage />} />
      </Routes>
      <BottomNav />
    </div>
  );
}
