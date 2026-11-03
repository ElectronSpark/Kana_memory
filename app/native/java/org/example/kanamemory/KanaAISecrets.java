package org.example.kanamemory;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.spec.MGF1ParameterSpec;
import java.io.File;
import java.nio.file.Files;
import org.json.JSONObject;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.OAEPParameterSpec;
import javax.crypto.spec.PSource;
import javax.crypto.spec.SecretKeySpec;

/** Device-local personal API credentials. No key material in backups or logs. */
public final class KanaAISecrets {
    private static final String ALIAS = "kanamemory.ai.credentials.v1";
    private static final String PREFS = "kanamemory_ai_credentials";
    private static final String IMPORT_ALIAS = "kanamemory.ai.import.v1";
    private KanaAISecrets() {}

    private static void checkName(String name) {
        if (!"openai_api_key".equals(name) && !name.matches("endpoint_[a-f0-9]{64}"))
            throw new IllegalArgumentException("Unknown credential");
    }

    private static SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true).build());
            generator.generateKey();
        }
        return (SecretKey) store.getKey(ALIAS, null);
    }

    public static synchronized void put(Context context, String name, String value) throws Exception {
        checkName(name);
        if (!("openai_api_key".equals(name) ? value.matches("sk-[A-Za-z0-9_-]{20,500}")
                : value.matches("[!-~]{1,2048}"))) throw new IllegalArgumentException("Invalid credential");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
        byte[] sealed = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
        String data = Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":" +
                Base64.encodeToString(sealed, Base64.NO_WRAP);
        if (!context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(name, data).commit())
            throw new IllegalStateException("Credential save failed");
    }

    public static synchronized String get(Context context, String name) throws Exception {
        checkName(name);
        String data = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(name, "");
        if (data.isEmpty()) return "";
        String[] parts = data.split(":", 2);
        if (parts.length != 2) throw new IllegalStateException("Invalid credential");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
        cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
        return new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }

    public static synchronized void remove(Context context, String name) {
        checkName(name);
        if (!context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(name).commit())
            throw new IllegalStateException("Credential removal failed");
    }

    public static synchronized void prepareImport(Context context) throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(IMPORT_ALIAS)) store.deleteEntry(IMPORT_ALIAS);
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA", "AndroidKeyStore");
        generator.initialize(new KeyGenParameterSpec.Builder(IMPORT_ALIAS, KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(2048).setDigests(KeyProperties.DIGEST_SHA256)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP).build());
        byte[] publicKey = generator.generateKeyPair().getPublic().getEncoded();
        byte[] nonce = new byte[24]; new SecureRandom().nextBytes(nonce);
        JSONObject request = new JSONObject();
        request.put("format", "kanamemory-device-key-import"); request.put("version", 1);
        request.put("public_key_spki", Base64.encodeToString(publicKey, Base64.NO_WRAP));
        request.put("nonce", Base64.encodeToString(nonce, Base64.NO_WRAP));
        request.put("expires_at_ms", System.currentTimeMillis() + 600000L);
        new File(context.getFilesDir(), "ai-key-import.json").delete();
        Files.write(new File(context.getFilesDir(), "ai-key-device.json").toPath(), request.toString().getBytes(StandardCharsets.UTF_8));
    }

    public static synchronized void completeImport(Context context) throws Exception {
        File requestFile = new File(context.getFilesDir(), "ai-key-device.json");
        File envelopeFile = new File(context.getFilesDir(), "ai-key-import.json");
        if (requestFile.length() > 8192 || envelopeFile.length() > 8192) throw new IllegalArgumentException("Import too large");
        JSONObject request = new JSONObject(new String(Files.readAllBytes(requestFile.toPath()), StandardCharsets.UTF_8));
        JSONObject envelope = new JSONObject(new String(Files.readAllBytes(envelopeFile.toPath()), StandardCharsets.UTF_8));
        if (request.getLong("expires_at_ms") < System.currentTimeMillis()
                || envelope.getInt("version") != 1
                || !"kanamemory-encrypted-key".equals(envelope.getString("format"))
                || !request.getString("nonce").equals(envelope.getString("nonce")))
            throw new IllegalArgumentException("Invalid or expired import");
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        Cipher unwrap = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
        unwrap.init(Cipher.DECRYPT_MODE, (PrivateKey) store.getKey(IMPORT_ALIAS, null),
                new OAEPParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA1, PSource.PSpecified.DEFAULT));
        byte[] rawKey = unwrap.doFinal(Base64.decode(envelope.getString("wrapped_key"), Base64.NO_WRAP));
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(rawKey, "AES"),
                new GCMParameterSpec(128, Base64.decode(envelope.getString("iv"), Base64.NO_WRAP)));
        cipher.updateAAD(request.getString("nonce").getBytes(StandardCharsets.UTF_8));
        byte[] plaintext = cipher.doFinal(Base64.decode(envelope.getString("ciphertext"), Base64.NO_WRAP));
        try { put(context, "openai_api_key", new String(plaintext, StandardCharsets.UTF_8)); }
        finally { java.util.Arrays.fill(rawKey, (byte) 0); java.util.Arrays.fill(plaintext, (byte) 0); }
        store.deleteEntry(IMPORT_ALIAS); requestFile.delete(); envelopeFile.delete();
    }
}
