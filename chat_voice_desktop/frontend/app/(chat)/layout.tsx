import { Toaster } from "sonner";
import { AppSidebar } from "@/components/chat/app-sidebar";
import { ChatShell } from "@/components/chat/shell";
import { DesktopMenuBar } from "@/components/chat/desktop-menu-bar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { VoiceProvider } from "@/components/voice/voice-provider";
import { ActiveChatProvider } from "@/hooks/use-active-chat";

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider className="h-svh flex-col overflow-hidden" defaultOpen={false}>
      <DesktopMenuBar />
      <div className="flex min-h-0 w-full flex-1">
        <AppSidebar user={{ id: "local-user" }} />
        <SidebarInset className="min-h-0">
          <Toaster position="top-center" theme="light" />
          <VoiceProvider>
            <ActiveChatProvider>
              <ChatShell />
            </ActiveChatProvider>
          </VoiceProvider>
          {children}
        </SidebarInset>
      </div>
    </SidebarProvider>
  );
}
