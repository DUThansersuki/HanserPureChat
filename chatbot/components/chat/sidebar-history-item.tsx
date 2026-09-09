import Link from "next/link";
import { memo, useCallback } from "react";
import type { Chat } from "@/lib/db/schema";
import { SidebarMenuButton, SidebarMenuItem } from "../ui/sidebar";

const PureChatItem = ({
  chat,
  isActive,
  setOpenMobile,
}: {
  chat: Chat;
  isActive: boolean;
  setOpenMobile: (open: boolean) => void;
}) => {
  const closeMobile = useCallback(() => setOpenMobile(false), [setOpenMobile]);

  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        asChild
        className="h-8 rounded-none text-[13px] text-sidebar-foreground/50 transition-all duration-150 hover:bg-transparent hover:text-sidebar-foreground data-active:bg-transparent data-active:font-normal data-active:text-sidebar-foreground/50 data-[active=true]:border-b data-[active=true]:border-dashed data-[active=true]:border-sidebar-foreground/50 data-[active=true]:font-medium data-[active=true]:text-sidebar-foreground"
        isActive={isActive}
      >
        <Link href={`/chat/${chat.id}`} onClick={closeMobile}>
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
