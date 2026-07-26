// Minimal @clerk/react mock so pages that call useUser / useAuth from Clerk
// don't crash at test time. Every hook returns a benign "signed-in, ready"
// state and Clerk components render nothing.

export const useUser = () => ({
  isLoaded: true,
  isSignedIn: true,
  user: { id: 'test-user', primaryEmailAddress: { emailAddress: 'test@aegis.local' } },
});

export const useAuth = () => ({
  isLoaded: true,
  isSignedIn: true,
  getToken: async () => 'test-token',
});

export const useOrganization = () => ({
  isLoaded: true,
  organization: { id: 'test-org', publicMetadata: {} },
  membership: { role: 'admin' },
});

export const SignIn = () => null;
export const SignUp = () => null;
export const ClerkProvider = ({ children }) => children;
