import { ref } from 'vue'

// The session currently visible at the latest message; historical browsing is
// not considered attended because new messages are not automatically read.
export const attendedChatSession = ref<number | null>(null)
