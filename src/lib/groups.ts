// The four reader groups and their chip labels in all three languages
// (prd.md > First-Visit Profile). One list, read by the welcome page, the
// per-bill chips and the groups panel, so a label can't differ between them.
//
// The English labels are the PRD's own. The Kiswahili and Sheng ones are
// short UI labels drafted 2026-10-23 and corrected by the learner the same
// day ("Mfanyikazi", "Msee wa Mboka", "Hustler / Sonko / Boss"; he is the
// Sheng gate); they are not generated text and carry no review badge.
import type { GroupId, Tri } from "./schema";

export const GROUPS: readonly { id: GroupId; label: Tri }[] = [
  { id: "employee", label: { en: "Employee / salaried", sw: "Mfanyikazi", sheng: "Msee wa Mboka" } },
  { id: "trader", label: { en: "Small trader / hustler", sw: "Mfanyabiashara mdogo", sheng: "Hustler / Sonko / Boss" } },
  { id: "transport", label: { en: "Boda / matatu / driver", sw: "Boda / matatu / dereva", sheng: "Boda / mat / dere" } },
  { id: "student", label: { en: "Student / youth", sw: "Mwanafunzi / kijana", sheng: "Student / youth" } },
];
