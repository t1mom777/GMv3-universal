import { GameMasterStudio } from "@/components/app/game-master-studio";
import { AppProviders } from "@/components/providers/app-providers";

export default function HomePage() {
  return (
    <AppProviders>
      <GameMasterStudio />
    </AppProviders>
  );
}
