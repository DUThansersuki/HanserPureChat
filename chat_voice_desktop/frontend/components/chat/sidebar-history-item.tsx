import Link from "next/link";
import { memo, useCallback } from "react";
import type { ChatOverview } from "@/lib/types";
import { SidebarMenuButton, SidebarMenuItem } from "../ui/sidebar";

const PureChatItem = ({
  chat,
  isActive,
  setOpenMobile,
}: {
  chat: ChatOverview;
  isActive: boolean;
  setOpenMobile: (open: boolean) => void;
}) => {
  const closeMobile = useCallback(() => setOpenMobile(false), [setOpenMobile]);

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        asChild
        className="h-9 rounded-md border-l-[3px] border-l-transparent text-[13px] text-sidebar-foreground transition-colors duration-150 hover:bg-sidebar-accent data-[active=true]:border-l-[var(--hanser-gold)] data-[active=true]:bg-[var(--brand-surface)] data-[active=true]:font-medium data-[active=true]:text-primary"
        isActive={isActive}
      >
        <Link href={`/chat/${chat.id}`} onClick={closeMobile} title={chat.title}>
          <span className="truncate">{chat.title}</span>
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
};

export const ChatItem = memo(
  PureChatItem,
  (previous, next) =>
    previous.isActive === next.isActive && previous.chat.id === next.chat.id
);
