import { NavLink } from "react-router-dom";
import { ShoppingBag, UtensilsCrossed, Users, Receipt } from "lucide-react";
import { cn } from "@/lib/utils";

const tabs = [
  { to: "/", label: "Pantry", icon: ShoppingBag },
  { to: "/meals", label: "Meals", icon: UtensilsCrossed },
  { to: "/receipts", label: "Receipt", icon: Receipt },
  { to: "/household", label: "Household", icon: Users },
];

export function BottomNav() {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-background border-t pb-safe">
      <div className="flex justify-around items-center h-16 max-w-lg mx-auto px-2">
        {tabs.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex flex-col items-center gap-1 px-3 py-2 rounded-lg text-xs font-medium transition-colors min-w-[60px]",
                isActive
                  ? "text-primary"
                  : "text-muted-foreground hover:text-foreground"
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon className={cn("h-5 w-5", isActive && "text-primary")} />
                <span>{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
