<#-- Copy of keycloak.v2's register.ftl with the collection notice added above the submit button (NFR-14, ADR-0043). The stock terms checkbox is removed, because a checkbox here would record nothing. -->
<#import "template.ftl" as layout>
<#import "field.ftl" as field>
<#import "user-profile-commons.ftl" as userProfileCommons>
<#import "password-validation.ftl" as validator>
<@layout.registrationLayout displayMessage=messagesPerField.exists('global') displayRequiredFields=true; section>
<!-- template: register.ftl -->

    <#if section = "header">
        <#if messageHeader??>
            ${msg(messageHeader)}
        <#else>
            ${msg("registerTitle")}
        </#if>
    <#elseif section = "form">
        <form id="kc-register-form" class="${properties.kcFormClass!}" action="${url.registrationAction}" method="post" novalidate="novalidate">
            <@userProfileCommons.userProfileFormFields; callback, attribute>
                <#if callback = "afterField">
                <#-- render password fields just under the username or email (if used as username) -->
                    <#if passwordRequired?? && (attribute.name == 'username' || (attribute.name == 'email' && realm.registrationEmailAsUsername))>
                        <@field.password name="password" required=true label=msg("password") autocomplete="new-password" />
                        <@field.password name="password-confirm" required=true label=msg("passwordConfirm") autocomplete="new-password" />
                    </#if>
                </#if>
            </@userProfileCommons.userProfileFormFields>

            <section id="kc-registration-notice" class="nptc-notice" aria-labelledby="kc-registration-notice-title">
                <h2 id="kc-registration-notice-title" class="nptc-notice-title">${msg("nptcRegisterNoticeTitle")}</h2>
                <ul class="nptc-notice-list">
                    <li>${msg("nptcRegisterNoticeCollect")}</li>
                    <li>${msg("nptcRegisterNoticeVisible")}</li>
                    <li>${msg("nptcRegisterNoticeRetention")}</li>
                    <li>${msg("nptcRegisterNoticeAccess")}</li>
                </ul>
                <#assign spaOrigin = ((realm.attributes.nptcFrontendBaseUrl)!'')?remove_ending('/')>
                <#if spaOrigin?has_content>
                    <p class="nptc-notice-links">
                        <a id="kc-privacy-link" href="${spaOrigin}/privacy" target="_blank" rel="noopener noreferrer">${msg("nptcRegisterPrivacyLink")}<span class="nptc-new-tab"> ${msg("nptcOpensInNewTab")}</span></a>
                        <a id="kc-terms-link" href="${spaOrigin}/terms" target="_blank" rel="noopener noreferrer">${msg("nptcRegisterTermsLink")}<span class="nptc-new-tab"> ${msg("nptcOpensInNewTab")}</span></a>
                    </p>
                </#if>
            </section>

            <#if recaptchaRequired?? && (recaptchaVisible!false)>
                <div class="form-group">
                    <div class="${properties.kcInputWrapperClass!}">
                        <div class="g-recaptcha" data-size="compact" data-sitekey="${recaptchaSiteKey}" data-action="${recaptchaAction}"></div>
                    </div>
                </div>
            </#if>

            <#if recaptchaRequired?? && !(recaptchaVisible!false)>
                <script>
                    function onSubmitRecaptcha(token) {
                        document.getElementById("kc-register-form").requestSubmit();
                    }
                </script>
                <div id="kc-form-buttons" class="${properties.kcFormButtonsClass!}">
                    <button class="${properties.kcButtonClass!} ${properties.kcButtonPrimaryClass!} ${properties.kcButtonBlockClass!} ${properties.kcButtonLargeClass!} g-recaptcha"
                            data-sitekey="${recaptchaSiteKey}" data-callback="onSubmitRecaptcha" data-action="${recaptchaAction}" type="submit" id="kc-submit">
                        ${msg("doRegister")}
                    </button>
                </div>
            <#else>
                <div id="kc-form-buttons" class="${properties.kcFormButtonsClass!}">
                    <input class="${properties.kcButtonClass!} ${properties.kcButtonPrimaryClass!} ${properties.kcButtonBlockClass!} ${properties.kcButtonLargeClass!}" type="submit" value="${msg("doRegister")}"/>
                </div>
            </#if>

            <div class="${properties.kcFormGroupClass!} pf-v5-c-login__main-footer-band">
                <div id="kc-form-options" class="${properties.kcFormOptionsClass!} pf-v5-c-login__main-footer-band-item">
                    <div class="${properties.kcFormOptionsWrapperClass!}">
                        <span><a href="${url.loginUrl}">${msg("backToLogin")}</a></span>
                    </div>
                </div>
            </div>

        </form>

        <@validator.templates/>
        <@validator.script field="password"/>
    </#if>
</@layout.registrationLayout>
